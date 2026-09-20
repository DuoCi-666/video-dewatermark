"""成功样本池：把「真实用户解析成功的链接」沉淀成巡检候选。

## 为什么需要它

巡检要真实链接，但开发者自己找链接是个持续负担：链接会失效、平台要覆盖，
而且新平台上线时手头往往没有可用链接。而每次真实解析成功，后端其实都经手过
一条**确实能用**的链接 —— 把它们沉淀下来，巡检样本就能被真实使用"养"出来，
不必手工收集。

## 与 failures 的分工

- ``failures``：记**失败**的链接（该修什么）
- ``samples`` ：记**成功**的链接（拿什么去巡检、以及哪些链接确实可用）

两者合起来形成闭环：失败样本用于修复，成功样本用于守住。

## 隐私

链接**原样保存**，不做任何参数剥离。原因：部分平台的分享参数是解析**必需**的
（如 doubao 视频链的 ``share_id``），剥离后样本将无法复现解析，失去巡检价值。
样本池只落本机 ``backend/data/``（已在 .gitignore），**绝不进仓库**，
导出接口仅内网可达。

## 环境变量

- ``SAMPLES_ENABLE``      是否收录，默认开（``0`` 关闭）
- ``SAMPLES_STORE_PATH``  存储路径，默认 ``backend/data/samples.jsonl``
- ``SAMPLES_MAX``         条数上限，默认 200
- ``SAMPLES_RETENTION_DAYS`` 保留天数，默认 30
- ``SAMPLES_MAX_BYTES``   文件大小上限，默认 1MiB
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("app.samples")

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "samples.jsonl"

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except ValueError:
        return default


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SampleStore:
    """成功样本池。按 (platform, url) 去重，累加命中次数。"""

    def __init__(
        self,
        path: Path | None = None,
        *,
        enabled: bool | None = None,
        max_records: int | None = None,
        retention_days: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.enabled = _env_flag("SAMPLES_ENABLE", True) if enabled is None else enabled
        self.path = path or Path(os.environ.get("SAMPLES_STORE_PATH") or DEFAULT_STORE)
        self.max_records = max_records or _env_int("SAMPLES_MAX", 200)
        self.retention_days = retention_days or _env_int("SAMPLES_RETENTION_DAYS", 30)
        self.max_bytes = max_bytes or _env_int("SAMPLES_MAX_BYTES", 1024 * 1024)
        self._items: dict[tuple[str, str], dict] = {}
        self._dirty = False
        if self.enabled:
            self._load()

    # ---------------------------------------------------------------- 读写
    def _load(self) -> None:
        try:
            text = self.path.read_text("utf-8")
        except OSError:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            url = item.get("url")
            platform = item.get("platform")
            if url and platform:
                self._items[(platform, url)] = item

    def record(self, *, platform: str, url: str, kind: str = "", source: str = "web") -> bool:
        """记一条成功解析的链接。返回是否改动（同链接重复命中只累加计数）。

        - 只收录 http(s) 链接；原样保存，不做任何参数剥离
          （剥离会让样本无法复现解析，如 doubao 视频链缺 share_id 即解析失败）
        - 关闭时直接返回 False，不做任何事
        """
        if not self.enabled:
            return False
        cleaned = (url or "").strip()
        if not cleaned.startswith("http"):
            return False

        key = (platform, cleaned)
        now = _now()
        item = self._items.get(key)
        if item is None:
            item = {
                "platform": platform,
                "url": cleaned,
                "kind": kind,
                "firstSeen": now,
                "lastSeen": now,
                "count": 0,
                "sources": {},
            }
            self._items[key] = item
        item["lastSeen"] = now
        item["count"] = int(item.get("count", 0)) + 1
        if kind:
            item["kind"] = kind
        sources = item.setdefault("sources", {})
        sources[source] = int(sources.get(source, 0)) + 1
        self._dirty = True
        self._trim()
        return True

    def _trim(self) -> None:
        """三重封顶：超条数 / 超天数即裁剪，优先丢最久未出现的。"""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        stale = []
        for key, item in self._items.items():
            try:
                if datetime.fromisoformat(item.get("lastSeen", "")) < cutoff:
                    stale.append(key)
            except ValueError:
                continue
        for key in stale:
            self._items.pop(key, None)
        if len(self._items) > self.max_records:
            ordered = sorted(
                self._items.items(), key=lambda kv: kv[1].get("lastSeen", ""), reverse=True
            )
            keep = dict(ordered[: self.max_records])
            self._items = keep

    def flush(self) -> None:
        """落盘（仅在内容有变化时写）。"""
        if not self.enabled or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                json.dumps(item, ensure_ascii=False)
                for item in sorted(
                    self._items.values(), key=lambda i: i.get("lastSeen", ""), reverse=True
                )
            ]
            payload = "\n".join(lines) + ("\n" if lines else "")
            if len(payload.encode("utf-8")) > self.max_bytes:
                # 超限：按 lastSeen 从新到旧截断到上限以内
                acc: list[str] = []
                size = 0
                for line in lines:
                    size += len(line.encode("utf-8")) + 1
                    if size > self.max_bytes:
                        break
                    acc.append(line)
                payload = "\n".join(acc) + ("\n" if acc else "")
            self.path.write_text(payload, "utf-8")
            self._dirty = False
        except OSError as exc:  # 落盘失败不影响解析主流程
            logger.warning("样本池落盘失败: %s", exc)

    def close(self) -> None:
        self.flush()

    # ---------------------------------------------------------------- 查询
    def items(self) -> list[dict]:
        return sorted(
            self._items.values(), key=lambda i: i.get("lastSeen", ""), reverse=True
        )

    def size(self) -> int:
        return len(self._items)

    def candidates(self, *, per_platform: int = 2, min_count: int = 1) -> dict[str, list[dict]]:
        """按平台给出巡检候选：每平台取最近命中的若干条。

        只取命中次数达标的（``min_count``）—— 命中过至少一次说明这条链接
        确实被成功解析过，比随机挑一条可靠。
        """
        grouped: dict[str, list[dict]] = {}
        for item in self.items():
            if int(item.get("count", 0)) < min_count:
                continue
            grouped.setdefault(item["platform"], []).append(item)
        return {k: v[:per_platform] for k, v in grouped.items()}

    def export_links(
        self, *, per_platform: int = 2, min_count: int = 1, fmt: str = "json"
    ) -> dict:
        """导出成巡检样本清单格式（可直接喂给 ops/links.json 或 VD_LINKS_EXTRA）。"""
        candidates = self.candidates(per_platform=per_platform, min_count=min_count)
        links: dict[str, dict] = {}
        for platform, items in sorted(candidates.items()):
            for idx, item in enumerate(items, start=1):
                name = f"{platform}-auto{idx}"
                links[name] = {
                    "_comment": f"自动收录：命中 {item.get('count', 0)} 次，"
                    f"最近 {item.get('lastSeen', '')}",
                    "platform": platform,
                    "expect": item.get("kind") or None,
                    "url": item["url"],
                }
        if fmt == "text":
            return {"text": json.dumps(links, ensure_ascii=False, indent=2), "count": len(links)}
        return {"links": links, "count": len(links)}

    def report(self) -> dict:
        by_platform: dict[str, int] = {}
        for item in self._items.values():
            by_platform[item["platform"]] = by_platform.get(item["platform"], 0) + 1
        return {
            "enabled": self.enabled,
            "total": len(self._items),
            "retentionDays": self.retention_days,
            "maxRecords": self.max_records,
            "byPlatform": dict(sorted(by_platform.items(), key=lambda kv: -kv[1])),
        }


# 全局单例（与 failures.SAMPLES / stats.STATS 一致的用法）
SAMPLES = SampleStore()


def _cli(argv: list[str] | None = None) -> int:
    """命令行入口：把样本池导出成巡检清单，供 VD_LINKS_EXTRA 使用。

        # 打印到 stdout（看一眼有哪些候选）
        python3 -m app.samples

        # 落到文件，再让巡检读它
        python3 -m app.samples -o /etc/video-dewatermark/samples.json
        VD_LINKS_EXTRA=/etc/video-dewatermark/samples.json ops/healthcheck.py
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m app.samples",
        description="把「真实用户成功解析过的链接」导出成巡检候选清单",
    )
    parser.add_argument("-o", "--output", help="输出文件路径（默认打印到标准输出）")
    parser.add_argument("--per-platform", type=int, default=2, help="每平台取几条，默认 2")
    parser.add_argument(
        "--min-count", type=int, default=1, help="至少命中几次才算候选，默认 1"
    )
    parser.add_argument("--report", action="store_true", help="先打印样本池概况")
    args = parser.parse_args(argv)

    if args.report:
        print(json.dumps(SAMPLES.report(), ensure_ascii=False, indent=2))

    result = SAMPLES.export_links(
        per_platform=max(args.per_platform, 1), min_count=max(args.min_count, 1), fmt="text"
    )
    if not result["count"]:
        print("样本池为空（还没有成功解析记录，或 SAMPLES_ENABLE=0）", file=sys.stderr)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result["text"], "utf-8")
        print(f"已写入 {result['count']} 条候选 → {args.output}", file=sys.stderr)
    else:
        print(result["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
