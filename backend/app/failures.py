"""解析失败样本：记录「哪条链接、为什么没解析成功」，用于事后复现与修复。

与 /api/stats 的分工
--------------------
``/api/stats`` 记的是**次数**（平台 A 今天失败几次、成功率多少）—— 它告诉你
"某个平台在变差"；这里记的是**样本**（具体哪条链接、错在哪一环）—— 它告诉你
"该怎么修"。

典型闭环：定期读 ``backend/data/failures.jsonl`` → 把样本交给 AI / 人工复现 →
修解析器 → 回来看这条链接是否还在出现。

去重
----
同一条链接反复失败**不重复写行**，只累加 ``count``、更新 ``lastSeen``、
把每次的定性累加到 ``kinds``。否则一条失效链接就能刷满整个文件，把真正的新问题挤掉。

定性（kind）
------------
- ``input``      输入没被识别（新平台 / 新分享格式）—— **最该关注**
- ``parser_bug`` 解析失败 / 超时（对方页面结构变了）
- ``risk``       触发平台风控
- ``gone``       作品不可用（多半内容被删，可忽略）

容量
----
条数 / 天数 / 文件大小三重封顶，超出后优先丢弃 ``gone``（作品已失效）与最久未出现的记录。

环境变量：FAILURES_STORE_PATH / FAILURES_MAX / FAILURES_RETENTION_DAYS /
FAILURES_MAX_BYTES / FAILURES_FLUSH_SEC
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("app.failures")

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "failures.jsonl"
INPUT_MAX = 300
MESSAGE_MAX = 120

# 错误码 → 定性。没列进来的（EMPTY_INPUT / INVALID_INPUT 等）视为噪声，不记录。
KIND_BY_CODE = {
    "INVALID_LINK": "input",
    "UNSUPPORTED_PLATFORM": "input",
    "PROFILE_LINK": "input",
    "WORK_UNAVAILABLE": "gone",
    "RISK_CONTROL": "risk",
    "PARSE_FAILED": "parser_bug",
    "PARSE_TIMEOUT": "parser_bug",
    "INTERNAL_ERROR": "parser_bug",
}

KIND_LABELS = {
    "input": "输入未被识别（新平台 / 新分享格式）· 最该修",
    "parser_bug": "解析器需要适配（对方页面结构变了）",
    "risk": "触发平台风控",
    "gone": "作品不可用（多半内容已删，可忽略）",
}

# 复查时的优先顺序：input 优先，gone 最后
KIND_ORDER = {"input": 0, "parser_bug": 1, "risk": 2, "gone": 3}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是整数，回退到默认值 %s", name, raw, default)
        return default


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Failures:
    def __init__(
        self,
        store_path: str | Path | None = None,
        *,
        max_records: int = 300,
        retention_days: int = 7,
        max_bytes: int = 2 * 1024 * 1024,
        flush_sec: int = 30,
    ) -> None:
        self.store_path = Path(store_path or os.environ.get("FAILURES_STORE_PATH") or DEFAULT_STORE)
        self.max_records = max(int(max_records), 1)
        self.retention_days = max(int(retention_days), 1)
        self.max_bytes = max(int(max_bytes), 4096)
        self.flush_sec = max(int(flush_sec), 1)
        self._items: dict[tuple[str, str], dict] = {}
        self._dirty = False
        self._flushed_at = time.monotonic()
        self._load()

    @classmethod
    def from_env(cls) -> "Failures":
        return cls(
            max_records=_env_int("FAILURES_MAX", 300),
            retention_days=_env_int("FAILURES_RETENTION_DAYS", 7),
            max_bytes=_env_int("FAILURES_MAX_BYTES", 2 * 1024 * 1024),
            flush_sec=_env_int("FAILURES_FLUSH_SEC", 30),
        )

    # ------------------------------------------------------------ 记录
    def record(
        self,
        *,
        url: str,
        input_text: str,
        platform: str,
        code: str,
        message: str,
        ms: int,
        source: str = "web",
        client_ip: str = "",
    ) -> bool:
        """记一条失败样本。返回是否被记录（噪声错误码会被忽略）。"""
        kind = KIND_BY_CODE.get(code)
        if kind is None:
            return False

        url = (url or "").strip()
        key = (platform, url or _clip(input_text, 160))
        now = _now()
        item = self._items.get(key)
        if item is None:
            item = {
                "platform": platform,
                "url": url,
                "input": _clip(input_text, INPUT_MAX),
                "firstSeen": now,
                "lastSeen": now,
                "count": 0,
                "kinds": {},
                "sources": {},
                "kind": kind,
                "lastCode": code,
                "lastMessage": _clip(message, MESSAGE_MAX),
                "lastMs": int(ms),
                "lastIp": client_ip,
            }
            self._items[key] = item
        item["count"] += 1
        item["lastSeen"] = now
        item["kind"] = kind  # 以最近一次的定性为准，方便按 kind 过滤
        item["kinds"][kind] = item["kinds"].get(kind, 0) + 1
        item["sources"][source] = item["sources"].get(source, 0) + 1
        item["lastCode"] = code
        item["lastMessage"] = _clip(message, MESSAGE_MAX)
        item["lastMs"] = int(ms)
        item["lastIp"] = client_ip
        if url:
            item["url"] = url

        self._dirty = True
        self._trim()
        if time.monotonic() - self._flushed_at >= self.flush_sec:
            self.flush()
        return True

    def resolve(self, url: str, *, platform: str | None = None) -> bool:
        """某条链接后来解析成功了 —— 原先记录的问题已消失，从待办里移除。

        这样 failures.jsonl 始终只包含「当前还没修好的」，复查（以及交给 AI 复现）时
        不会被已经修好的条目干扰。删除是立即落盘的，避免刚修好又被读到。
        """
        if not url:
            return False
        keys = [
            key
            for key in self._items
            if key[1] == url and (platform is None or key[0] == platform)
        ]
        if not keys:
            return False
        for key in keys:
            self._items.pop(key, None)
        self._dirty = True
        self.flush()
        return True

    def _trim(self) -> None:        # 1) 超期
        deadline = (datetime.now() - timedelta(days=self.retention_days)).isoformat(timespec="seconds")
        for key in [k for k, item in self._items.items() if item["lastSeen"] < deadline]:
            self._items.pop(key, None)
            self._dirty = True
        # 2) 超量：丢掉最久没出现的
        if len(self._items) > self.max_records:
            ordered = sorted(self._items.items(), key=lambda kv: kv[1]["lastSeen"])
            for key, _ in ordered[: len(self._items) - self.max_records]:
                self._items.pop(key, None)
                self._dirty = True

    # ------------------------------------------------------------ 落盘
    def _serialize(self, items: list[dict]) -> str:
        return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)

    def flush(self) -> None:
        if not self._dirty:
            return
        items = self._ordered()
        # 3) 超字节：从末尾丢（_ordered 已按"价值 + 新旧"排序，末尾正是 gone / 最久未出现的）
        while len(items) > 1 and len(self._serialize(items).encode("utf-8")) > self.max_bytes:
            items.pop()
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store_path.with_name(self.store_path.name + ".tmp")
            tmp.write_text(self._serialize(items), "utf-8")
            tmp.replace(self.store_path)  # 原子替换
            self._dirty = False
            self._flushed_at = time.monotonic()
        except OSError as exc:
            logger.warning("失败样本落盘失败：%s", exc)

    def _load(self) -> None:
        try:
            raw = self.store_path.read_text("utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("失败样本读取失败：%s", exc)
            return
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                key = (item.get("platform", ""), item.get("url") or _clip(item.get("input", ""), 160))
                self._items[key] = item
            except (ValueError, AttributeError) as exc:
                logger.warning("失败样本某行损坏，已跳过：%s", exc)
        self._dirty = False

    def close(self) -> None:
        self.flush()

    def reset(self) -> None:
        """仅用于测试：清空内存状态（不删文件）。"""
        self._items = {}
        self._dirty = False
        self._flushed_at = time.monotonic()

    def size(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------ 查询
    def _ordered(self, kinds: set[str] | None = None) -> list[dict]:
        items = [item for item in self._items.values() if not kinds or item.get("kind") in kinds]
        # 先按"该不该修"排，再按最近出现排，保证复查时重要的在最前面
        return sorted(
            items,
            key=lambda item: (KIND_ORDER.get(item.get("kind", ""), 9), _neg_ts(item.get("lastSeen", ""))),
        )

    def report(self, *, limit: int = 50, kinds: set[str] | None = None) -> dict:
        ordered = self._ordered(kinds)
        by_kind: dict[str, int] = {}
        by_platform: dict[str, int] = {}
        for item in ordered:
            by_kind[item.get("kind", "?")] = by_kind.get(item.get("kind", "?"), 0) + 1
            by_platform[item["platform"]] = by_platform.get(item["platform"], 0) + 1
        return {
            "ok": True,
            "checkedAt": _now(),
            "total": len(ordered),
            "returned": min(len(ordered), max(limit, 1)),
            "retentionDays": self.retention_days,
            "maxRecords": self.max_records,
            "summary": {
                "byKind": dict(sorted(by_kind.items(), key=lambda kv: KIND_ORDER.get(kv[0], 9))),
                "byPlatform": dict(sorted(by_platform.items(), key=lambda kv: -kv[1])),
                "totalOccurrences": sum(item.get("count", 0) for item in ordered),
            },
            "items": ordered[: max(limit, 1)],
        }


def _neg_ts(stamp: str) -> str:
    """把时间戳转成"越小越新"的排序键（配合升序排序实现按时间倒序）。"""
    return "".join(chr(255 - ord(ch)) for ch in stamp[:19])


SAMPLES = Failures.from_env()


def render_text(report: dict) -> str:
    """把样本渲染成适合直接丢给 AI 的文本块（自带说明与字段含义）。"""
    lines = [
        "以下是 video-dewatermark 解析失败样本（已去重，按「该不该修」排序）。",
        "每条是一例没能解析成功的链接，请据此复现并判断解析器需要怎么改。",
        "字段：kind 定性 | count 出现次数 | code 错误码 | ms 耗时 | url 提取出的链接 | input 用户原始输入",
        "kind 含义：input=输入未被识别(最该修) / parser_bug=解析器需适配 / risk=触发风控 / gone=作品已失效(可忽略)",
        "",
        f"生成时间 {report['checkedAt']}　共 {report['total']} 条样本"
        f"（保留 {report['retentionDays']} 天，上限 {report['maxRecords']} 条）",
    ]
    if report["summary"]["byKind"]:
        lines.append(
            "分布：" + "　".join(f"{k}={v}" for k, v in report["summary"]["byKind"].items())
        )
    lines.append("")

    if not report["items"]:
        lines.append("（暂无失败样本）")
        return "\n".join(lines) + "\n"

    for index, item in enumerate(report["items"], start=1):
        lines.append(
            f"[{index}] kind={item.get('kind')}  {item['platform']}  出现 {item.get('count')} 次"
            f"  最近 {item.get('lastSeen')}"
        )
        lines.append(f"    code={item.get('lastCode')}  message={item.get('lastMessage')}  耗时={item.get('lastMs')}ms")
        lines.append(f"    url: {item.get('url') or '(未提取到链接)'}")
        lines.append(f"    input: {item.get('input') or '(空)'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
