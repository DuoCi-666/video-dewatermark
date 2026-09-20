#!/usr/bin/env python3
"""巡检趋势追踪：从「瞬时状态」升级为「成功率趋势」。

## 为什么需要它

原巡检只保留最新一份状态（health_status.json）+ 每次追加完整报告
（health.log）。这能回答「现在挂了吗」，但答不了两个更关键的问题：

1. **这是偶发抖动还是持续退化？** 连续失败计数会在成功后清零，
   看不出「这个平台这两天成功率从 98% 掉到 60%」这种缓慢劣化 —— 而平台风控
   变化几乎都是渐进的（先偶尔 403，再频繁 403，最后彻底失效）。
2. **失败的是解析器还是链接？** 巡检用的是固定链接，链接本身会过期
   （作品被删、口令失效）。固定链接失效和解析器失效，表现完全一样。
   若同一平台配了多条样本链接，就能区分：多条都挂 = 解析器问题；
   只有一条挂 = 那条链接过期。

## 做法

每次巡检后，把「精简到平台粒度」的结果追加到 `health_history.jsonl`
（一行一次，只记 ok/fail 与失败明细），并据此算出每平台的近期成功率与
「退化」判定。完整报告仍在 health.log，两者分工：历史看趋势，日志查现场。

成功率窗口取最近 N 次（默认 20），只统计该平台确实被检查过的次数 ——
新增平台不会因为历史空白而被判定退化。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# 趋势窗口：最近多少次巡检参与成功率计算。20 次 × 默认 30 分钟 ≈ 10 小时，
# 既能抓住「今天下午开始变差」，又不会被前几天的一次抖动长期带偏。
TREND_WINDOW = 20
# 退化告警的静默周期：同一平台在「状态期内」只提醒一次。
# 为什么需要它：窗口是 20 次（≈10 小时），一旦某平台滑到 80% 以下，
# 每轮巡检（30 分钟）都会重新判定为「退化」，于是在窗口滑出前会连发 ~20 封
# 一模一样的邮件（2026-09-19 huya 504 抖动即如此，从早 7 点连发到中午）——
# 告警疲劳比漏报更危险。加 6 小时静默：一个巡检班次最多提醒一次，
# 若平台恢复（率回到阈值以上）后再次变差，则重置为「首次」立即再报。
DEGRADE_SILENCE_MINUTES = 360
# 历史文件最多保留多少行（超出裁掉最旧的）。20 次窗口 × 若干平台，
# 保留 500 行足够回看，也让文件始终很小（每行几百字节）。
HISTORY_MAX_LINES = 500
# 退化判定：窗口内样本数达到该值才评估（太少没有统计意义）
DEGRADE_MIN_SAMPLES = 5
# 成功率低于该阈值即视为「退化」（区别于「已连续失败」的硬故障）
DEGRADE_RATE = 0.8


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_history(
    path: Path,
    checked_at: str,
    platforms: dict[str, dict],
    *,
    max_lines: int = HISTORY_MAX_LINES,
) -> None:
    """追加一次精简记录：{ts, platforms: {name: {ok, platform, detail}}}。

    只记巡检关心的最小集合，避免文件膨胀；详情在现场日志 health.log 里。
    """
    record = {
        "ts": checked_at,
        "platforms": {
            name: {
                "ok": bool(item.get("ok")),
                "platform": item.get("platform") or "",
                "detail": (item.get("detail") or "")[:200],
            }
            for name, item in platforms.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim(path, max_lines)


def _trim(path: Path, max_lines: int) -> None:
    """只保留最近 max_lines 行（超出裁掉最旧的）。

    用「读入内存再重写」而不是逐行删除：文件本就被限制在很小规模
    （几百行），一次重写最简单且不会有边界 bug。
    """
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= max_lines:
        return
    path.write_text("\n".join(lines[-max_lines:]) + "\n", "utf-8")


def load_history(path: Path) -> list[dict]:
    """读取历史记录（忽略损坏行，保证单行写坏不影响整体）。"""
    try:
        text = path.read_text("utf-8")
    except OSError:
        return []
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def platform_trends(records: list[dict], *, window: int = TREND_WINDOW) -> dict[str, dict]:
    """按平台聚合窗口内的成功率。

    返回 {name: {samples, ok, rate, last_ok, streak_fail, details}}。
    只在窗口内被检查过的平台才出现（新增平台不因历史空白被判退化）。
    """
    recent = records[-window:]
    trends: dict[str, dict] = {}
    for record in recent:
        for name, item in (record.get("platforms") or {}).items():
            entry = trends.setdefault(
                name,
                {
                    "platform": item.get("platform") or "",
                    "samples": 0,
                    "ok": 0,
                    "last_ok": None,
                    "streak_fail": 0,
                    "details": [],
                },
            )
            entry["samples"] += 1
            entry["platform"] = item.get("platform") or entry["platform"]
            if item.get("ok"):
                entry["ok"] += 1
                entry["last_ok"] = True
                entry["streak_fail"] = 0
            else:
                entry["last_ok"] = False
                entry["streak_fail"] += 1
                detail = item.get("detail") or ""
                if detail and detail not in entry["details"]:
                    entry["details"].append(detail)
                    del entry["details"][5:]  # 只留最近几种失败原因
    for entry in trends.values():
        samples = entry["samples"] or 1
        entry["rate"] = round(entry["ok"] / samples, 4)
    return trends


def degraded_platforms(
    trends: dict[str, dict],
    *,
    min_samples: int = DEGRADE_MIN_SAMPLES,
    rate: float = DEGRADE_RATE,
) -> list[dict]:
    """挑出「正在退化但尚未连续失败到告警阈值」的平台。

    这类平台最容易漏掉：当前这次可能是成功的，所以不进 failures 列表；
    但窗口成功率已经明显偏低，说明在缓慢劣化，值得提前提示。

    返回按成功率升序排列（最差的在前）。
    """
    out = []
    for name, trend in trends.items():
        if trend["samples"] < min_samples:
            continue
        if trend["rate"] >= rate:
            continue
        # 已经连续失败的平台会走原有告警路径，这里只补「间歇性失败」这类
        if trend["streak_fail"] >= min_samples:
            continue
        out.append(
            {
                "name": name,
                "platform": trend["platform"],
                "samples": trend["samples"],
                "ok": trend["ok"],
                "rate": trend["rate"],
                "streak_fail": trend["streak_fail"],
                "details": trend["details"],
            }
        )
    out.sort(key=lambda item: (item["rate"], -item["streak_fail"]))
    return out


def build_trend_section(
    trends: dict[str, dict],
    degraded: list[dict],
    *,
    window: int = TREND_WINDOW,
    min_samples: int = DEGRADE_MIN_SAMPLES,
) -> dict:
    """组装写进巡检报告的 trend 区块（供状态文件与告警文案复用）。

    三个计数相加 = evaluated，口径不重叠也不遗漏：
      - healthy：窗口成功率达标
      - degraded：成功率偏低且间歇性（当前可能成功）—— 本模块新引入的告警对象
      - hard_down：成功率偏低且最近连续失败 —— 走原有「连续失败」告警路径
    """
    enough = [t for t in trends.values() if t["samples"] >= min_samples]
    total = len(enough)
    healthy = sum(1 for t in enough if t["rate"] >= DEGRADE_RATE)
    degraded_names = {item["name"] for item in degraded}
    hard_down = sum(
        1
        for name, t in trends.items()
        if t["samples"] >= min_samples
        and t["rate"] < DEGRADE_RATE
        and name not in degraded_names
    )
    return {
        "window": window,
        "min_samples": min_samples,
        "evaluated": total,
        "healthy": healthy,
        "degraded": degraded,
        "hardDown": hard_down,
    }


def render_trend_lines(report: dict, *, limit: int = 5) -> list[str]:
    """把 trend 区块渲染成告警里的几行文字（无退化时返回空）。"""
    trend = report.get("trend") or {}
    degraded = trend.get("degraded") or []
    if not degraded:
        return []
    lines = [
        f"⚠ 检出 {len(degraded)} 个平台近期成功率偏低（最近 {trend.get('window')} 次巡检）:"
    ]
    for item in degraded[:limit]:
        pct = round(item["rate"] * 100)
        lines.append(
            f"· {item['name']}({item['platform']}) 成功率 {pct}%"
            f"（{item['ok']}/{item['samples']}，最近连续失败 {item['streak_fail']} 次）"
        )
        if item.get("details"):
            lines.append(f"    最近原因：{item['details'][0]}")
    return lines


def _parse_iso(value: str) -> datetime | None:
    """宽松解析 ISO 时间戳，失败返回 None（坏数据不能让巡检崩掉）。"""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def split_degraded_alerts(
    degraded: list[dict],
    prev_state: dict | None,
    *,
    now: datetime | str | None = None,
    silence_minutes: int = DEGRADE_SILENCE_MINUTES,
) -> tuple[list[dict], dict]:
    """把退化平台分成「该告警」与「静默」两拨，并给出新的状态快照。

    prev_state 是本模块上一轮写回的状态快照 {name: {rate, samples, ...}}；
    返回值 second 元素是要落盘的新快照（含 last_alert_ts / last_rate）。

    去重规则（逐平台独立判定）：
      · 不在 prev_state 里            → 首次出现，告警
      · 上轮也退化、静默未到期        → 持续中，静默（不再打扰）
      · 上轮也退化、静默已到期        → 仍差，放行再提醒一次
      · 用了 silence_minutes <= 0     → 关闭静默，行为与改造前完全一致

    「持续」的判据同时看时间与快照是否仍在窗口内：若中间恢复过，
    healthcheck 会把这台平台的条目从 snapshots 里删掉，下次出现即「首次」。
    """
    prev_state = prev_state or {}
    moment = now if isinstance(now, datetime) else _parse_iso(now or "") if now else None
    if moment is None:
        moment = datetime.now(timezone.utc).astimezone()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    to_alert: list[dict] = []
    snapshots: dict[str, dict] = {}
    for item in degraded:
        name = item["name"]
        prior = prev_state.get(name) or {}
        last_alert = _parse_iso(prior.get("last_alert_ts") or "")
        if last_alert is None:
            # 首次出现（或上轮状态无法解析）→ 视为新状态期，立即告警
            to_alert.append(item)
            last_alert = moment
        else:
            elapsed = (moment - last_alert).total_seconds() / 60.0
            if silence_minutes <= 0 or elapsed >= silence_minutes:
                to_alert.append(item)
                last_alert = moment
        snapshots[name] = {
            "rate": item["rate"],
            "samples": item["samples"],
            "last_alert_ts": last_alert.isoformat(timespec="seconds"),
        }
    return to_alert, snapshots


# ------------------------------------------------------------------ 多样本聚合
# 同一平台常配多条样本链接（如 kuaishou-video / kuaishou-images / kuaishou-xxx）。
# 单条失败时无法判断「是解析器坏了，还是这条链接本身过期了」—— 两者表现完全一样。
# 有了多条样本就能区分：
#   · 全部失败  -> 解析器 / 平台侧问题（该修解析器）
#   · 仅部分失败、原因是 4xx -> 失败的那些多半是链接过期（该换链接，不必动代码）
#   · 仅部分失败、原因是超时/连接层 -> 上游瞬时抖动（勿换链接，观察即可）
# 这个判定直接决定告警该不该发、以及该指向哪个动作，避免「链接过期」「瞬时抖动」
# 被混为一谈、半夜把人叫起来去换一个其实没坏的链接。


def group_by_platform(platforms: dict[str, dict]) -> dict[str, dict]:
    """把条目级结果按平台聚合。

    返回 {platform_key: {name, samples, ok, fail, failed_names, failed_details}}。
    """
    groups: dict[str, dict] = {}
    for name, item in platforms.items():
        key = item.get("platform") or name
        group = groups.setdefault(
            key,
            {
                "platform": key,
                "samples": 0,
                "ok": 0,
                "fail": 0,
                "failed_names": [],
                "failed_details": [],
            },
        )
        group["samples"] += 1
        if item.get("ok"):
            group["ok"] += 1
        else:
            group["fail"] += 1
            group["failed_names"].append(name)
            detail = item.get("detail") or ""
            if detail and detail not in group["failed_details"]:
                group["failed_details"].append(detail)
                del group["failed_details"][5:]
    return groups


def _is_transient_group(group: dict) -> bool:
    """该平台的失败原因是否全是「连接 / 超时」层（而非确定性 4xx）。

    只看确定性信号：失败明细里只要出现 HTTP 403/404/410 这类 4xx，就说明是
    鉴权失效或资源删除，属于「真过期」；反之，明细是 5xx（504 = 应用把 httpx
    超时转成的网关超时）或 HTTP 0 / 0 bytes 这类无状态码的失败，就是上游瞬时
    抖动（复测即恢复，不该提示换链接）。
    """
    details = group.get("failed_details") or []
    if not details:
        return False
    return not any("HTTP 4" in detail for detail in details)


def classify_failures(platforms: dict[str, dict]) -> dict:
    """按「平台粒度」判定失败性质，供告警与报告使用。

    输出四类：
      all_down   —— 该平台本周期的样本全军覆没，判定为解析器 / 平台侧问题（需处理）
      partial    —— 部分样本失败且原因是确定性 4xx，判定为链接过期（换链接即可）
      flaky      —— 部分样本失败但原因是连接/超时层，判定为上游瞬时抖动（观察，勿换链接）
      healthy    —— 全部通过
    外加单条样本平台无法区分时的保守归类（只有 1 条且失败 -> all_down）。
    """
    groups = group_by_platform(platforms)
    all_down, partial, flaky, healthy = [], [], [], []
    for key, group in groups.items():
        entry = {
            "platform": key,
            "samples": group["samples"],
            "ok": group["ok"],
            "fail": group["fail"],
            "failed_names": list(group["failed_names"]),
            "details": list(group["failed_details"]),
        }
        if group["fail"] == 0:
            healthy.append(entry)
        elif group["ok"] == 0:
            # 全挂：多条样本同时失效的概率极低，判定为解析器 / 平台问题
            all_down.append(entry)
        elif _is_transient_group(group):
            # 部分挂、且失败原因清一色是连接/超时层：这是上游瞬时抖动，
            # 不是链接过期 —— 重试即可恢复，绝不能提示「换链接」。
            flaky.append(entry)
        else:
            # 部分挂、失败原因是确定性 4xx（鉴权/作品删除）：更可能是那条链接过期
            partial.append(entry)
    all_down.sort(key=lambda e: e["platform"])
    partial.sort(key=lambda e: e["platform"])
    flaky.sort(key=lambda e: e["platform"])
    return {"allDown": all_down, "partial": partial, "flaky": flaky, "healthy": healthy}


def render_multisample_lines(report: dict, *, limit: int = 5) -> list[str]:
    """把多样本判定渲染成告警里的几行（无部分失败时返回空）。

    partial（确定性 4xx）与 flaky（连接/超时抖动）分开措辞：前者才建议换链接，
    后者明确提示「勿换链接」，避免误报把人引向无效动作。
    """
    section = report.get("sampleCoverage") or {}
    partial = section.get("partial") or []
    flaky = section.get("flaky") or []
    lines: list[str] = []
    if partial:
        lines.append(
            f"ℹ 另有 {len(partial)} 个平台仅部分样本失败（失败原因含 4xx，"
            f"多半是该条链接已过期，建议更换，不必改解析器）："
        )
        for item in partial[:limit]:
            lines.append(
                f"· {item['platform']} {item['ok']}/{item['samples']} 通过，"
                f"失败样本：{', '.join(item['failed_names'])}"
            )
            if item.get("details"):
                lines.append(f"    原因：{item['details'][0]}")
    if flaky:
        lines.append(
            f"ℹ 另有 {len(flaky)} 个平台仅部分样本失败，失败原因均为连接/超时层"
            f"（上游瞬时抖动，无需换链接、无需改解析器，请观察）："
        )
        for item in flaky[:limit]:
            lines.append(
                f"· {item['platform']} {item['ok']}/{item['samples']} 通过，"
                f"失败样本：{', '.join(item['failed_names'])}"
            )
            if item.get("details"):
                lines.append(f"    原因：{item['details'][0]}")
    return lines
