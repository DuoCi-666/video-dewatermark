"""解析调用统计：按「天 × 平台」聚合调用量、成功率与耗时。

定位：ops/healthcheck.py 是**定点抽查**（每 30 分钟拿固定链接各试一次），
这里记录的是**每一次真实解析请求**。两者互补 —— 抽查能立刻发现"挂了"，
统计能看出"哪个平台在悄悄变差"（成功率下滑、耗时变长）。

设计
----
- 内存计数，每 ``STATS_FLUSH_SEC`` 秒（或进程退出时）原子落盘到
  ``backend/data/stats.json``；只保留最近 ``STATS_RETENTION_DAYS`` 天。
- 耗时指标只统计**未命中缓存**的请求，反映真实解析成本；用蓄水池抽样
  保留最近 ``SAMPLE_CAP`` 个样本用于 P95，避免样本无限增长。
- ``/api/stats`` 默认只允许**未经反向代理直连**的请求（即本机运维）。
  nginx 用 ``proxy_set_header`` 强制写入 X-Real-IP、并给 X-Forwarded-For
  追加上游链，所以只要出现这两个头就一定是来自外部的代理请求（客户端
  自行伪造同样会被判定为"来自代理"而拒绝）。需要远程查看时可设置
  ``STATS_TOKEN``，然后用 ``X-Stats-Token`` 头访问。

环境变量：STATS_STORE_PATH / STATS_RETENTION_DAYS / STATS_FLUSH_SEC /
STATS_TOKEN / STATS_LOCAL_HOSTS
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Request

logger = logging.getLogger("app.stats")

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "stats.json"
SAMPLE_CAP = 2000
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
# 非法/不支持的输入单独归到这一桶，既能观察扫描行为，又不污染平台成功率
REJECTED = "_rejected"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是整数，回退到默认值 %s", name, raw, default)
        return default


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


class Bucket:
    """单个「天 × 平台」的计数桶。"""

    __slots__ = ("ok", "fail", "cached", "parsed", "ms_total", "ms_max", "errors", "sources", "seen", "samples")

    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0
        self.cached = 0
        # 未命中缓存、真的去抓了平台的次数
        self.parsed = 0
        self.ms_total = 0
        self.ms_max = 0
        self.errors: dict[str, int] = {}
        self.sources: dict[str, int] = {}
        self.seen = 0
        self.samples: list[int] = []

    def record(self, *, ok: bool, ms: int, cached: bool, error: str | None, source: str) -> None:
        self.sources[source] = self.sources.get(source, 0) + 1
        if ok:
            self.ok += 1
        else:
            self.fail += 1
            if error:
                self.errors[error] = self.errors.get(error, 0) + 1
        if cached:
            self.cached += 1
            return  # 缓存命中的耗时没有参考价值，不计入耗时指标
        self.parsed += 1
        self.ms_total += ms
        if ms > self.ms_max:
            self.ms_max = ms
        self._sample(ms)

    def _sample(self, ms: int) -> None:
        """蓄水池抽样：样本满了之后仍能无偏地反映整体分布。"""
        self.seen += 1
        if len(self.samples) < SAMPLE_CAP:
            self.samples.append(ms)
            return
        index = random.randrange(self.seen)
        if index < SAMPLE_CAP:
            self.samples[index] = ms

    def merge(self, other: "Bucket") -> None:
        self.ok += other.ok
        self.fail += other.fail
        self.cached += other.cached
        self.parsed += other.parsed
        self.ms_total += other.ms_total
        self.ms_max = max(self.ms_max, other.ms_max)
        for key, value in other.errors.items():
            self.errors[key] = self.errors.get(key, 0) + value
        for key, value in other.sources.items():
            self.sources[key] = self.sources.get(key, 0) + value
        self.seen += other.seen
        self.samples.extend(other.samples)
        if len(self.samples) > SAMPLE_CAP:
            del self.samples[SAMPLE_CAP:]

    def metrics(self) -> dict:
        total = self.ok + self.fail
        return {
            "total": total,
            "ok": self.ok,
            "fail": self.fail,
            "cached": self.cached,
            "parsed": self.parsed,
            "successRate": round(self.ok / total, 4) if total else None,
            "avgMs": round(self.ms_total / self.parsed) if self.parsed else None,
            "p95Ms": _percentile(self.samples, 0.95),
            "maxMs": self.ms_max,
            "bySource": dict(sorted(self.sources.items(), key=lambda kv: -kv[1])),
            "errors": dict(sorted(self.errors.items(), key=lambda kv: -kv[1])),
        }

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "fail": self.fail,
            "cached": self.cached,
            "parsed": self.parsed,
            "msTotal": self.ms_total,
            "msMax": self.ms_max,
            "errors": self.errors,
            "sources": self.sources,
            "seen": self.seen,
            "samples": self.samples,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Bucket":
        bucket = cls()
        bucket.ok = int(data.get("ok", 0))
        bucket.fail = int(data.get("fail", 0))
        bucket.cached = int(data.get("cached", 0))
        bucket.parsed = int(data.get("parsed", 0))
        bucket.ms_total = int(data.get("msTotal", 0))
        bucket.ms_max = int(data.get("msMax", 0))
        bucket.errors = {str(k): int(v) for k, v in (data.get("errors") or {}).items()}
        bucket.sources = {str(k): int(v) for k, v in (data.get("sources") or {}).items()}
        bucket.seen = int(data.get("seen", len(data.get("samples") or [])))
        bucket.samples = [int(v) for v in (data.get("samples") or [])][:SAMPLE_CAP]
        return bucket


class Stats:
    def __init__(self, store_path: str | Path | None = None, *, retention_days: int = 7, flush_sec: int = 30) -> None:
        self.store_path = Path(store_path or os.environ.get("STATS_STORE_PATH") or DEFAULT_STORE)
        self.retention_days = max(int(retention_days), 1)
        self.flush_sec = max(int(flush_sec), 1)
        self._days: dict[str, dict[str, Bucket]] = {}
        self._dirty = False
        self._flushed_at = time.monotonic()
        self._load()

    @classmethod
    def from_env(cls) -> "Stats":
        return cls(
            retention_days=_env_int("STATS_RETENTION_DAYS", 7),
            flush_sec=_env_int("STATS_FLUSH_SEC", 30),
        )

    # ------------------------------------------------------------ 记录
    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def record(
        self,
        platform: str,
        *,
        ok: bool,
        ms: int,
        cached: bool = False,
        error: str | None = None,
        source: str = "web",
    ) -> None:
        day = self._today()
        bucket = self._days.setdefault(day, {}).setdefault(platform, Bucket())
        bucket.record(ok=ok, ms=max(int(ms), 0), cached=cached, error=error, source=source)
        self._dirty = True
        self._prune()
        if self._dirty and time.monotonic() - self._flushed_at >= self.flush_sec:
            self.flush()

    def _prune(self) -> None:
        today = datetime.now()
        keep = {(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(self.retention_days)}
        for day in [d for d in self._days if d not in keep]:
            self._days.pop(day, None)
            self._dirty = True

    # ------------------------------------------------------------ 落盘
    def flush(self) -> None:
        if not self._dirty:
            return
        snapshot = {
            "version": 1,
            "savedAt": datetime.now().isoformat(timespec="seconds"),
            "days": {day: {p: b.to_dict() for p, b in buckets.items()} for day, buckets in self._days.items()},
        }
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store_path.with_name(self.store_path.name + ".tmp")
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), "utf-8")
            tmp.replace(self.store_path)  # 原子替换，避免半截文件
            self._dirty = False
            self._flushed_at = time.monotonic()
        except OSError as exc:
            logger.warning("统计落盘失败：%s", exc)

    def _load(self) -> None:
        try:
            raw = self.store_path.read_text("utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("统计读取失败：%s", exc)
            return
        try:
            data = json.loads(raw)
            for day, buckets in (data.get("days") or {}).items():
                self._days[day] = {p: Bucket.from_dict(b) for p, b in buckets.items()}
        except (ValueError, AttributeError, TypeError) as exc:
            logger.warning("统计文件损坏，已忽略：%s", exc)
            return
        self._prune()
        self._dirty = False

    def close(self) -> None:
        self.flush()

    def reset(self) -> None:
        """仅用于测试：清空内存状态（不删文件）。"""
        self._days = {}
        self._dirty = False
        self._flushed_at = time.monotonic()

    def size(self) -> int:
        return sum(len(buckets) for buckets in self._days.values())

    # ------------------------------------------------------------ 查询
    def report(self, *, date: str | None = None, days: int | None = None) -> dict:
        if date:
            wanted = [date]
        elif days:
            today = datetime.now()
            wanted = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(max(days, 1))]
        else:
            wanted = sorted(self._days)

        totals: dict[str, Bucket] = {}
        for day in wanted:
            for platform, bucket in self._days.get(day, {}).items():
                totals.setdefault(platform, Bucket()).merge(bucket)

        platforms = {p: b.metrics() for p, b in totals.items()}
        overall = Bucket()
        for platform, bucket in totals.items():
            if platform == REJECTED:
                continue
            overall.merge(bucket)

        return {
            "ok": True,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "retentionDays": self.retention_days,
            "days": sorted(self._days),
            "window": wanted,
            "overall": overall.metrics(),
            "rejected": platforms.pop(REJECTED, None),
            "platforms": dict(
                sorted(platforms.items(), key=lambda kv: -(kv[1]["total"] or 0))
            ),
        }


STATS = Stats.from_env()


def local_hosts() -> set[str]:
    raw = os.environ.get("STATS_LOCAL_HOSTS", "").strip()
    if not raw:
        return LOCAL_HOSTS
    return LOCAL_HOSTS | {item.strip() for item in raw.split(",") if item.strip()}


def is_local(request: Request) -> bool:
    """是否为「未经代理直连」的本机请求（见模块 docstring 的安全依据）。"""
    token = os.environ.get("STATS_TOKEN", "").strip()
    if token and request.headers.get("x-stats-token") == token:
        return True
    if (request.headers.get("x-real-ip") or "").strip():
        return False
    if (request.headers.get("x-forwarded-for") or "").strip():
        return False
    host = request.client.host if request.client else ""
    return host in local_hosts()


def render_text(report: dict) -> str:
    """供 `curl /api/stats?fmt=text` 直接阅读的表格。"""
    lines = [
        f"解析统计  {report['checkedAt']}   保留 {report['retentionDays']} 天",
        f"覆盖日期: {', '.join(report['window']) or '（暂无数据）'}",
        "",
        f"{'平台':<16}{'调用':>7}{'成功':>7}{'失败':>7}{'缓存':>7}{'成功率':>9}{'平均':>9}{'P95':>9}{'最大':>9}",
        "-" * 82,
    ]

    def row(name: str, m: dict) -> str:
        rate = "—" if m["successRate"] is None else f"{m['successRate'] * 100:.1f}%"
        avg = "—" if m["avgMs"] is None else f"{m['avgMs']}ms"
        return (
            f"{name:<16}{m['total']:>7}{m['ok']:>7}{m['fail']:>7}{m['cached']:>7}"
            f"{rate:>9}{avg:>9}{m['p95Ms']:>8}ms{m['maxMs']:>8}ms"
        )

    lines.append(row("全部平台", report["overall"]))
    for platform, metrics in report["platforms"].items():
        lines.append(row(platform, metrics))

    if report.get("rejected"):
        metrics = report["rejected"]
        lines.append("")
        lines.append(f"（非法/不支持的输入 {metrics['total']} 次，未计入成功率）")
        for code, count in list(metrics["errors"].items())[:5]:
            lines.append(f"    {code}: {count}")

    sources: dict[str, int] = {}
    for metrics in [report["overall"], *report["platforms"].values()]:
        for source, count in metrics["bySource"].items():
            sources[source] = sources.get(source, 0) + count
    if sources:
        lines.append("")
        lines.append("来源：" + "  ".join(f"{k}={v}" for k, v in sorted(sources.items(), key=lambda kv: -kv[1])))

    return "\n".join(lines) + "\n"


def render_health_report(stats_report: dict, failures_report: dict) -> str:
    """把「统计」与「失败样本」合成为一份可直接投喂给 AI 的诊断报告。

    差异：``render_text`` 是给人看的表格，``failures.render_text`` 是给人看的样本清单；
    这里是给模型看的**单一上下文** —— 一次拿到「现状（成功率/耗时）+ 待修样本（含原始输入）」，
    把两个接口的往返与手工拼接省掉（诊断闭环：投喂 → 定位 → 修解析器 → 看统计回升）。
    """
    lines = [
        "# video-dewatermark 解析诊断报告",
        "",
        "你是 video-dewatermark（聚合视频去水印解析工具站）的维护者。下面是该站最近的",
        "解析统计与失败样本，请据此判断「哪个平台在变差、失败的根因大概是什么、下一步该改哪里」。",
        "",
        f"生成时间 {stats_report.get('checkedAt')}　统计保留 {stats_report.get('retentionDays')} 天",
        "",
        "## 一、解析统计（按平台聚合，只统计真实请求；巡检流量单列不计入成功率）",
        "",
    ]

    def row(name: str, m: dict) -> str:
        rate = "—" if m["successRate"] is None else f"{m['successRate'] * 100:.1f}%"
        avg = "—" if m["avgMs"] is None else f"{m['avgMs']}ms"
        return (
            f"{name:<16}{m['total']:>7}{m['ok']:>7}{m['fail']:>7}{m['cached']:>7}"
            f"{rate:>9}{avg:>9}{m['p95Ms']:>8}ms{m['maxMs']:>8}ms"
        )

    lines.append(f"{'平台':<16}{'调用':>7}{'成功':>7}{'失败':>7}{'缓存':>7}{'成功率':>9}{'平均':>9}{'P95':>9}{'最大':>9}")
    lines.append("-" * 82)
    lines.append(row("全部平台", stats_report["overall"]))
    for platform, metrics in stats_report["platforms"].items():
        lines.append(row(platform, metrics))

    if stats_report.get("rejected"):
        rejected = stats_report["rejected"]
        lines.append("")
        lines.append(f"（非法/不支持的输入 {rejected['total']} 次，未计入成功率）")

    lines.append("")
    lines.append("说明：调用=总请求数；缓存=命中 60s 缓存的次数（其耗时无参考价值，已从平均/P95/最大中剔除）；")
    lines.append("      成功率 = 成功 / 调用；P95/最大仅反映未命中缓存的真实解析耗时。")

    # ---------------------------------------------------------------- 失败样本
    lines.append("")
    lines.append("## 二、待修失败样本（已去重，按「该不该修」排序）")
    lines.append("")
    summary = failures_report.get("summary", {})
    lines.append(
        f"共 {failures_report.get('total', 0)} 条样本，"
        f"累计出现 {summary.get('totalOccurrences', 0)} 次"
        f"（保留 {failures_report.get('retentionDays')} 天，上限 {failures_report.get('maxRecords')} 条）"
    )
    by_kind = summary.get("byKind") or {}
    if by_kind:
        lines.append("定性分布：" + "　".join(f"{k}={v}" for k, v in by_kind.items()))
    lines.append("")
    lines.append("字段：kind 定性 | count 出现次数 | code 错误码 | ms 耗时 | url 提取出的链接 | input 用户原始输入")
    lines.append("kind 含义：input=输入未被识别(新平台/新格式，最该修) / parser_bug=解析器需适配(页面结构变了) / risk=触发风控 / gone=作品已失效(可忽略)")
    lines.append("")

    items = failures_report.get("items") or []
    if not items:
        lines.append("（暂无失败样本）")
    for index, item in enumerate(items, start=1):
        lines.append(
            f"[{index}] kind={item.get('kind')}  {item.get('platform')}  出现 {item.get('count')} 次"
            f"  最近 {item.get('lastSeen')}"
        )
        lines.append(f"    code={item.get('lastCode')}  message={item.get('lastMessage')}  耗时={item.get('lastMs')}ms")
        lines.append(f"    url: {item.get('url') or '(未提取到链接)'}")
        lines.append(f"    input: {item.get('input') or '(空)'}")
        lines.append("")

    lines.append("## 三、请回答")
    lines.append("1. 哪些平台的成功率/耗时值得警惕（结合调用量与失败样本量判断，别只看单次失败）？")
    lines.append("2. 每条 pending 样本的失败根因是什么（输入问题 / 解析器问题 / 风控 / 作品已删）？")
    lines.append("3. 按「性价比」给出修复优先级：先改哪个解析器、改哪里、需要什么新样本复现？")
    return "\n".join(lines).rstrip() + "\n"
