"""批量打包：把一次批量解析的所有成功结果打成 ZIP 供一次性下载。

设计要点：
- 后台任务异步打包，前端轮询进度（PackState）；
- 字节直接经 media.open_media 从上游拉，落临时文件后再写 zip；
- 媒体本身已是压缩格式，用 ZIP_STORED，不做二次压缩；
- 单条超限只丢弃该条，整体超限才中止，避免一条烂源拖垮整个打包；
- 打包产物有 TTL，过期由 _pack_sweep 清理。
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import batch

# 单个文件上限（视频/图母盘本身已压缩，硬限制防超大单条撑爆内存）
ZIP_ITEM_MAX = int(os.environ.get("BATCH_ZIP_ITEM_MAX_BYTES", str(250 * 1024 * 1024)))
# 整个 zip 上限：达到即中止打包并报错，避免一次性抓爆磁盘
ZIP_ARCHIVE_MAX = int(os.environ.get("BATCH_ZIP_MAX_BYTES", str(1024 * 1024 * 1024)))
# 打包下载的并发（拉取上游字节的全局并发，独立于解析并发）
PACK_CONCURRENCY = max(int(os.environ.get("BATCH_ZIP_CONCURRENCY", "2")), 1)
# 打包产物保留时长（秒），超时自动清理
PACK_TTL_SEC = int(os.environ.get("BATCH_ZIP_TTL_SEC", str(30 * 60)))
# 打包缓存的下载并发信号量（module 级，import 时惰性绑定 loop）
_PACK_SEM = asyncio.Semaphore(PACK_CONCURRENCY)


async def _open_media(token: str, range_header: str | None = None):
    """经 app.main 转发到 media.open_media。

    走一层 main 是为了兼容历史用法 `monkeypatch.setattr("app.main._open_media", ...)`
    —— 测试与外部脚本用这个路径替换回源实现，直接 import 会绕开补丁。
    """
    from app import main

    return await main._open_media(token, range_header)
_PACKS: dict[str, "PackState"] = {}
_PACK_ORDER: list[str] = []


def pack_dir() -> Path:
    override = os.environ.get("PACK_STORE_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data" / "packs"


@dataclass
class PackState:
    """一个批量 job 的打包进度。download 前先经 open_media 拉字节到临时文件。"""

    job_id: str
    total: int
    status: str = "running"  # running | done | failed
    pulled: int = 0  # 已成功拉取的文件数
    failed: int = 0  # 拉取失败/超限的文件数
    bytes: int = 0  # 已拉取的字节
    error: str = ""
    zip_path: str = ""
    zip_size: int = 0
    created_at: float = 0.0
    updated_iso: str = ""

    def as_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "status": self.status,
            "total": self.total,
            "pulled": self.pulled,
            "failed": self.failed,
            "bytes": self.bytes,
            "error": self.error,
            "zipSize": self.zip_size,
            "canDownload": self.status == "done" and bool(self.zip_path) and self.zip_size > 0,
        }


def _pack_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _pack_sweep() -> None:
    now = time.monotonic()
    expired = [
        jid
        for jid, p in _PACKS.items()
        if p.status == "done" and now - p.created_at >= PACK_TTL_SEC
    ]
    for jid in expired:
        pack = _PACKS.pop(jid, None)
        if pack and pack.zip_path and Path(pack.zip_path).is_file():
            try:
                os.remove(pack.zip_path)
            except OSError:
                pass
    _PACK_ORDER[:] = [jid for jid in _PACK_ORDER if jid in _PACKS]


def get_pack(job_id: str) -> PackState | None:
    _pack_sweep()
    return _PACKS.get(job_id)


def start_pack(job_id: str, total: int) -> PackState:
    """登记并启动一次打包；同一 job 已有进行中的打包则复用（幂等）。"""
    state = PackState(job_id=job_id, total=total, created_at=time.monotonic())
    _PACKS[job_id] = state
    _PACK_ORDER.append(job_id)
    return state


def _filename_safe(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name or "").strip(" ._")
    return cleaned or fallback


def _media_token(url: str) -> str | None:
    prefix = "/api/media/"
    if url and url.startswith(prefix):
        return url[len(prefix):]
    return None


def collect_media(job_snapshot: dict) -> list[tuple[str, str, str]]:
    """从批量 job 的成功结果里收集 (token, 文件名, 子目录)，用于打包。

    只取「主媒体」控制体积与超时：视频取默认档，图片取全部原图；封面不打包。
    返回空表示没有可打包的媒体。
    """
    entries: list[tuple[str, str, str]] = []
    for item in job_snapshot.get("items", []):
        result = item.get("result")
        if not item.get("ok") or not isinstance(result, dict):
            continue
        title = result.get("title") or f"item_{item.get('index', 0) + 1}"
        folder = f"{item.get('index', 0) + 1:02d}_{_filename_safe(str(title), str(item.get('index', 0) + 1))}"
        kind = result.get("type")
        if kind == "video":
            variants = result.get("variants") or []
            first = variants[0] if variants else None
            token = _media_token(first.get("mediaUrl")) if first else None
            if token:
                entries.append((token, "video.mp4", folder))
        elif kind == "images":
            for idx, image in enumerate(result.get("images") or [], start=1):
                token = _media_token(image.get("previewUrl"))
                if token:
                    entries.append((token, f"image_{idx}.jpg", folder))
    return entries


async def run_pack(pack: PackState, job_id: str) -> None:
    """后台打包：按条拉取上游字节到临时文件，再打包成 zip（ZIP_STORED，不重复压缩）。"""
    job_snapshot = batch.STORE.get(job_id)
    tmp_root: Path | None = None
    downloaded: list[tuple[str, Path]] = []  # (arcname, tmp_path)
    try:
        if job_snapshot is None:
            pack.status, pack.error = "failed", "批量任务不存在或已过期"
            return
        media = collect_media(job_snapshot)
        pack.total = len(media)
        if not media:
            pack.status, pack.error = "failed", "没有可打包的媒体"
            return

        tmp_root = Path(tempfile.mkdtemp(prefix=f"pack_{job_id}_", dir=str(pack_dir().parent)))
        used_names: set[str] = set()

        async def _fetch(idx: int, token: str, filename: str, folder: str) -> tuple[str, Path] | None:
            arcname = f"{folder}/{filename}"
            base = arcname.rsplit("/", 1)[0]
            leaf = arcname.rsplit("/", 1)[1]
            n = 1
            while arcname in used_names:
                arcname = f"{base}/{Path(leaf).stem}_{n}{Path(leaf).suffix}"
                n += 1
            used_names.add(arcname)

            tmp_path = tmp_root / f"{idx:04d}.blob"
            client, upstream = await _open_media(token)
            if upstream is None or client is None:
                from app.clients import get_proxy_client

                if client is not None and client is not get_proxy_client():
                    await client.aclose()
                return None
            try:
                with open(tmp_path, "wb") as fh:
                    size = 0
                    async for chunk in upstream.aiter_bytes():
                        size += len(chunk)
                        if size > ZIP_ITEM_MAX:
                            return None  # 单条超限：放弃该文件，不整体失败
                        fh.write(chunk)
                        pack.bytes += len(chunk)
                        pack.updated_iso = _pack_now_iso()
                        if pack.bytes > ZIP_ARCHIVE_MAX:
                            pack.status, pack.error = "failed", "打包体积过大，已中止"
                            return None
                    if size == 0:
                        return None
                return arcname, tmp_path
            finally:
                await upstream.aclose()
                from app.clients import get_proxy_client

                if client is not get_proxy_client():
                    await client.aclose()

        async def _guarded(idx: int, token: str, filename: str, folder: str):
            async with _PACK_SEM:
                if pack.status != "running":
                    return None
                return await _fetch(idx, token, filename, folder)

        results = await asyncio.gather(
            *[_guarded(idx, t, f, fo) for idx, (t, f, fo) in enumerate(media)],
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, tuple):
                downloaded.append(res)  # type: ignore[arg-type]
            else:
                pack.failed += 1
                if pack.status == "running":
                    pack.updated_iso = _pack_now_iso()

        pack.pulled = len(downloaded)
        if pack.status == "failed":
            return
        if not downloaded:
            pack.status, pack.error = "failed", "媒体拉取失败，请稍后重试"
            return

        # 组装 zip：媒体已是压缩格式，用 STORED 避免重复压缩与 CPU 浪费
        pack_dir().mkdir(parents=True, exist_ok=True)
        zip_path = pack_dir() / f"batch_{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for arcname, tmp in downloaded:
                zf.write(tmp, arcname=arcname)
        pack.zip_path = str(zip_path)
        pack.zip_size = zip_path.stat().st_size
        pack.status = "done"
    except Exception:
        pack.status = "failed"
        pack.error = "打包失败，请稍后重试"
    finally:
        pack.updated_iso = _pack_now_iso()
        if tmp_root is not None and tmp_root.is_dir():
            import shutil

            shutil.rmtree(tmp_root, ignore_errors=True)
