"""媒体代理：把上游 CDN 的媒体流经本站转发给浏览器。

为什么需要代理：
- 隐私 —— 浏览器不直连平台 CDN，平台拿不到访问者 IP；
- 跨域 —— 平台 CDN 普遍不带 CORS 头，直连 <video> 播不了；
- 防盗链 —— 部分 CDN 校验 Referer，需要服务端按域名补正确的头；
- 统一入口 —— 短时效签名直链失效时，由服务端自动重新解析换新地址。

HLS（m3u8）源额外由 ffmpeg 转封装成 mp4：浏览器 <video> 不能直接播 m3u8。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from fastapi.responses import FileResponse, StreamingResponse

from app import tokens
from app.clients import get_proxy_client
from app.errors import DOWNLOAD_FAILED

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

PROXY_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=15.0)

# 媒体回源的抗抖动：上游 CDN 偶发返回 404/5xx 或「状态码正常但读不到字节」时，
# 同一个候选原地重试一次，再换下一个候选（详见 _open_media）。
UPSTREAM_ATTEMPTS = 2
UPSTREAM_RETRY_INTERVAL = 0.8

# —— HLS(m3u8) 转封装 ——
# 部分平台（A站、央视网长节目等）只提供 HLS 源，浏览器 <video> 无法直接播放。
# 由媒体代理调用 ffmpeg 把 m3u8 转封装成 fMP4 流式返回（-c copy，不重编码），
# 预览与下载都直接拿到 mp4。HLS_TRANSCODE=0 可关闭；FFMPEG_BIN 可指定二进制。
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
HLS_TRANSCODE = os.environ.get("HLS_TRANSCODE", "1").lower() not in ("0", "false", "no")
FFMPEG_AVAILABLE = shutil.which(FFMPEG_BIN) is not None

# HLS 转存的全局并发上限：每次转存都会拉起一个 ffmpeg 进程（吃 CPU 与磁盘），
# 必须限流，避免并发下载把机器打满。超出上限的请求排队等待，而不是直接失败。
HLS_TRANSCODE_CONCURRENCY = max(int(os.environ.get("HLS_TRANSCODE_CONCURRENCY", "2")), 1)
_HLS_SEM = asyncio.Semaphore(HLS_TRANSCODE_CONCURRENCY)
# 单次转存的时间上限（秒）：上游异常慢时放弃转存并回退流式，避免长期占着闸门
HLS_TRANSCODE_TIMEOUT_SEC = max(int(os.environ.get("HLS_TRANSCODE_TIMEOUT_SEC", "600")), 1)

# HLS 转存缓存：把 m3u8 完整转成 mp4 落盘后，即可支持 Range（拖动进度条）与完整下载。
HLS_CACHE_DIR = Path(
    os.environ.get("HLS_CACHE_PATH")
    or str(Path(__file__).resolve().parent.parent / "data" / "hls")
)
HLS_CACHE_TTL_SEC = int(os.environ.get("HLS_CACHE_TTL_SEC", str(60 * 60)))
_HLS_FILES: dict[str, tuple[str, float]] = {}  # token -> (path, created_at)
_HLS_TASKS: dict[str, asyncio.Task] = {}


def _upstream_headers(url: str, range_header: str | None = None) -> dict[str, str]:
    """回源请求头。默认不带 Referer —— 抖音 CDN 有 Referer 防盗链，外域 Referer 会 403。"""
    headers: dict[str, str] = {"Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("bilivideo.com") or host.endswith("hdslb.com") or host.endswith(
        "bilibili.com"
    ):
        headers["Referer"] = "https://www.bilibili.com/"
    # 微博 CDN（f.video.weibocdn.com 视频 / wx*.sinaimg.cn 图集）不带 Referer 会 403
    elif host.endswith("weibocdn.com") or host.endswith("sinaimg.cn"):
        headers["Referer"] = "https://weibo.com/"
    # 即梦 CDN（v3-dreamnia.jimeng.com）校验 Referer：外域 Referer 会 403，
    # 无 Referer 或站内 Referer 均 206。默认不带 Referer 已可正常回源，
    # 这里补站内 Referer 作为兜底，避免上游后续收紧为「必须有 Referer」。
    elif host.endswith("jimeng.com"):
        headers["Referer"] = "https://jimeng.jianying.com/"
    return headers


async def open_media(token: str, range_header: str | None = None):
    """打开媒体上游流：候选重试 + 签名失效刷新。返回 (client, upstream_response)。

    - 供 /api/media /api/download（流式转发）与批量 ZIP 打包（读字节）共用；
    - upstream 可能为 None（媒体不可用 / 候选全失败）；此时调用方负责关闭自建 client；
    - 返回的 client 是共享代理客户端或本次自建的临时的，调用方拿到 response 记得 aclose。
    """
    item = tokens.get(token)
    if item is None:
        return None, None
    shared = get_proxy_client()
    own_client = shared is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "*/*"},
            follow_redirects=True,
            timeout=PROXY_TIMEOUT,
        )
    else:
        client = shared

    async def _try(urls: list[str]) -> httpx.Response | None:
        for url in urls:
            for attempt in range(UPSTREAM_ATTEMPTS):
                try:
                    request = client.build_request(
                        "GET", url, headers=_upstream_headers(url, range_header)
                    )
                    candidate = await client.send(request, stream=True)
                except httpx.HTTPError:
                    candidate = None
                if candidate is not None:
                    if candidate.status_code < 400:
                        # 状态码正常：只有确实拿到内容才算成功；Content-Length 为 0
                        # （图床抽风）直接换下一个 CDN 候选，不在原地重试
                        if candidate.headers.get("content-length") != "0":
                            return candidate
                        await candidate.aclose()
                        break
                    await candidate.aclose()
                    # 4xx 里只有 404/408/429 值得原地重试（CDN 偶发抽风）；
                    # 403/410 等是签名失效或资源已删，重试没用，直接换下一个候选 / 触发刷新
                    if 400 <= candidate.status_code < 500 and candidate.status_code not in (
                        404,
                        408,
                        429,
                    ):
                        break
                if attempt < UPSTREAM_ATTEMPTS - 1:
                    await asyncio.sleep(UPSTREAM_RETRY_INTERVAL)
        return None

    upstream = await _try(item.source_urls)
    if upstream is None and item.refresh:
        # 短时效签名：候选全部失效说明直链过期了，重新解析换一批新地址再试一次
        fresh = await refresh_media(item)
        if fresh:
            upstream = await _try(fresh)
    return client, upstream


def looks_like_hls(url: str, content_type: str | None) -> bool:
    """判断上游媒体是否为 HLS（m3u8）：按 content-type 或 URL 后缀。"""
    if "mpegurl" in (content_type or "").lower():
        return True
    return urlparse(url).path.lower().endswith(".m3u8")


def _to_mp4_name(name: str) -> str:
    """把文件名扩展名统一成 .mp4（HLS 转封装后的产物）。"""
    base = name.rsplit(".", 1)[0] if "." in (name or "") else (name or "")
    return f"{base or 'video'}.mp4"


async def _ffmpeg_mp4_stream(url: str, referer: str | None = None):
    """调用 ffmpeg 把 HLS 转封装为 fMP4 并流式产出（-c copy，不重编码）。"""
    args = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-user_agent", UA]
    if referer:
        args += ["-headers", f"Referer: {referer}\r\n"]
    args += [
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.returncode is None:
            proc.kill()
        with suppress(Exception):
            await proc.wait()


def _hls_cache_sweep() -> None:
    now = time.monotonic()
    for token, (path, created) in list(_HLS_FILES.items()):
        if now - created >= HLS_CACHE_TTL_SEC:
            _HLS_FILES.pop(token, None)
            with suppress(OSError):
                os.remove(path)


def _hls_cached(token: str) -> str | None:
    _hls_cache_sweep()
    entry = _HLS_FILES.get(token)
    if entry and Path(entry[0]).is_file():
        return entry[0]
    return None


async def _hls_transcode(token: str, url: str, referer: str | None) -> str | None:
    """把 HLS 完整转存成可 seek 的 mp4（+faststart，moov 前置）。"""
    HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = str(HLS_CACHE_DIR / f"{token}.mp4")
    args = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-user_agent", UA]
    if referer:
        args += ["-headers", f"Referer: {referer}\r\n"]
    args += [
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-y", dest,
    ]
    async with _HLS_SEM:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=HLS_TRANSCODE_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            # 上游是慢速/恶意源：超时直接放弃，回退到流式路径，不无限挂着
            proc.kill()
            with suppress(Exception):
                await proc.wait()
            rc = -1
    target = Path(dest)
    if rc == 0 and target.is_file() and target.stat().st_size > 0:
        _HLS_FILES[token] = (dest, time.monotonic())
        return dest
    with suppress(OSError):
        target.unlink()
    return None


def _hls_file_task(token: str, url: str, referer: str | None) -> asyncio.Task:
    """同一 token 只跑一次转存任务。"""
    _hls_cache_sweep()
    task = _HLS_TASKS.get(token)
    if task is None:
        task = asyncio.ensure_future(_hls_transcode(token, url, referer))
        _HLS_TASKS[token] = task

        def _done(_task: asyncio.Task, key: str = token) -> None:
            _HLS_TASKS.pop(key, None)

        task.add_done_callback(_done)
    return task


async def _hls_response(token: str, item, url: str, as_attachment: bool):
    """HLS（m3u8）源：有转存文件就走文件（支持 Range / 拖动进度条），否则流式。"""
    filename = _to_mp4_name(item.filename)
    referer = _upstream_headers(url).get("Referer")

    cached = _hls_cached(token)
    if cached:
        if as_attachment:
            return FileResponse(cached, media_type="video/mp4", filename=filename)
        return FileResponse(cached, media_type="video/mp4")

    if as_attachment:
        # 下载：等转存完成，保证拿到完整 mp4
        path = await _hls_file_task(token, url, referer)
        if path:
            return FileResponse(path, media_type="video/mp4", filename=filename)
    else:
        # 预览：先流式保证快速起播，同时后台转存；转完后再次请求即可 seek
        if token not in _HLS_TASKS:
            _hls_file_task(token, url, referer)

    out_headers = {"Content-Type": "video/mp4"}
    if as_attachment:
        out_headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return StreamingResponse(_ffmpeg_mp4_stream(url, referer), headers=out_headers)


async def _close_if_owned(client: httpx.AsyncClient) -> None:
    """只关闭本次自建的临时 client，共享 client 交给 lifespan 管理。"""
    if client is not get_proxy_client():
        await client.aclose()


async def proxy(token: str, as_attachment: bool, range_header: str | None = None):
    """/api/media 与 /api/download 的统一实现。"""
    item = tokens.get(token)
    if item is None:
        raise DOWNLOAD_FAILED

    client, upstream = await open_media(token, range_header)
    if upstream is None or client is None:
        raise DOWNLOAD_FAILED

    # HLS（m3u8）源：浏览器 <video> 不能直接播，交给 ffmpeg 转封装成 mp4
    upstream_url = str(upstream.url)
    if FFMPEG_AVAILABLE and HLS_TRANSCODE and looks_like_hls(
        upstream_url, upstream.headers.get("content-type")
    ):
        await upstream.aclose()
        await _close_if_owned(client)
        return await _hls_response(token, item, upstream_url, as_attachment)

    out_headers: dict[str, str] = {
        "Content-Type": upstream.headers.get("content-type") or "application/octet-stream",
    }
    if upstream.headers.get("accept-ranges"):
        out_headers["Accept-Ranges"] = upstream.headers["accept-ranges"]
    if upstream.headers.get("content-range"):
        out_headers["Content-Range"] = upstream.headers["content-range"]
    content_length = upstream.headers.get("content-length")
    if content_length:
        out_headers["Content-Length"] = content_length
    if as_attachment:
        filename = quote(item.filename)
        out_headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await _close_if_owned(client)

    return StreamingResponse(stream(), headers=out_headers, status_code=upstream.status_code)


async def refresh_media(item) -> list[str] | None:
    """媒体直链失效时重新解析，返回一批新地址（由 parsing 注入的具体实现）。

    为避免 media ←→ parsing 循环导入，这里用延迟导入在调用时才取函数。
    """
    from app.parsing import refresh_media_urls

    return await refresh_media_urls(item)
