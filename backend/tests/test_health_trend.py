"""巡检趋势追踪（ops/health_trend.py）的单元测试。

ops/health_trend.py 是独立脚本模块（不依赖后端包），这里用 importlib
按路径加载，避免把它塞进 Python 包或用 sys.path 污染。

覆盖：
- 历史追加 / 裁剪 / 损坏行容错
- 成功率窗口聚合（只统计被检查过的平台，新增平台不被历史空白拖累）
- 退化判定（间歇性失败会被抓出；连续硬故障交给原有告警路径，不重复报）
- 告警文案渲染
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_OPS = Path(__file__).resolve().parents[2] / "ops"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "health_trend", _OPS / "health_trend.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


trend = _load_module()


def _record(ts: str, **platforms) -> dict:
    return {
        "ts": ts,
        "platforms": {
            name: {"ok": ok, "platform": name, "detail": detail}
            for name, (ok, detail) in platforms.items()
        },
    }


# ------------------------------------------------------------------ 历史文件
def test_append_then_load_roundtrip(tmp_path):
    path = tmp_path / "h.jsonl"
    trend.append_history(
        path,
        "2024-01-01T00:00:00+08:00",
        {"p": {"ok": True, "platform": "p", "detail": "fine"}},
    )
    records = trend.load_history(path)
    assert len(records) == 1
    assert records[0]["platforms"]["p"]["ok"] is True


def test_append_trims_to_max_lines(tmp_path):
    path = tmp_path / "h.jsonl"
    for i in range(10):
        trend.append_history(
            path,
            f"2024-01-01T00:00:{i:02d}+08:00",
            {"p": {"ok": True, "platform": "p", "detail": ""}},
            max_lines=3,
        )
    records = trend.load_history(path)
    assert len(records) == 3
    # 保留的应是最新的三条
    assert records[-1]["ts"].endswith(":09+08:00")


def test_load_history_skips_corrupt_lines(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text(
        '{"ts":"a","platforms":{}}\n'
        "not json at all\n"
        "\n"
        '{"ts":"b","platforms":{}}\n',
        "utf-8",
    )
    records = trend.load_history(path)
    assert [r["ts"] for r in records] == ["a", "b"]


def test_load_history_missing_file_is_empty(tmp_path):
    assert trend.load_history(tmp_path / "nope.jsonl") == []


def test_append_history_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "nested" / "h.jsonl"
    trend.append_history(path, "t", {"p": {"ok": True, "platform": "p", "detail": ""}})
    assert path.is_file()


# ------------------------------------------------------------------ 成功率聚合
def test_platform_trends_computes_rate():
    records = [
        _record("t1", kuaishou=(True, "")),
        _record("t2", kuaishou=(True, "")),
        _record("t3", kuaishou=(False, "HTTP 403")),
        _record("t4", kuaishou=(True, "")),
    ]
    trends = trend.platform_trends(records)
    entry = trends["kuaishou"]
    assert entry["samples"] == 4
    assert entry["ok"] == 3
    assert entry["rate"] == 0.75
    assert entry["streak_fail"] == 0  # 最近一次成功


def test_platform_trends_counts_trailing_failures():
    records = [
        _record("t1", p=(True, "")),
        _record("t2", p=(False, "e1")),
        _record("t3", p=(False, "e2")),
    ]
    entry = trend.platform_trends(records)["p"]
    assert entry["streak_fail"] == 2
    assert entry["rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_platform_trends_respects_window():
    records = [_record(f"t{i}", p=(False, "old")) for i in range(10)]
    records += [_record(f"ok{i}", p=(True, "")) for i in range(10)]
    entry = trend.platform_trends(records, window=10)["p"]
    # 窗口只看最近 10 次（全是成功）
    assert entry["samples"] == 10
    assert entry["rate"] == 1.0


def test_platform_trends_only_includes_checked_platforms():
    """新增平台不该因为历史记录里没有它而被算成 0 成功率。"""
    records = [_record("t1", old=(True, ""))]
    trends = trend.platform_trends(records)
    assert "old" in trends
    assert "brand-new" not in trends


def test_platform_trends_keeps_distinct_failure_details():
    records = [
        _record("t1", p=(False, "HTTP 403")),
        _record("t2", p=(False, "HTTP 403")),
        _record("t3", p=(False, "超时")),
    ]
    entry = trend.platform_trends(records)["p"]
    assert entry["details"] == ["HTTP 403", "超时"]


# ------------------------------------------------------------------ 退化判定
def test_degraded_catches_intermittent_failure():
    """间歇失败（成功率低但当前这次可能是成功的）应被判定退化。"""
    records = [_record(f"f{i}", p=(False, "HTTP 403")) for i in range(4)]
    records += [_record("ok", p=(True, ""))]
    records += [_record("ok2", p=(True, ""))]
    trends = trend.platform_trends(records)
    degraded = trend.degraded_platforms(trends)
    assert [d["name"] for d in degraded] == ["p"]


def test_degraded_ignores_healthy_platform():
    records = [_record(f"t{i}", p=(True, "")) for i in range(10)]
    assert trend.degraded_platforms(trend.platform_trends(records)) == []


def test_degraded_ignores_too_few_samples():
    """样本太少（如刚新增的平台）不做统计判断。"""
    records = [_record("t1", p=(False, "boom"))]
    assert trend.degraded_platforms(trend.platform_trends(records)) == []


def test_degraded_skips_hard_down_platform():
    """连续失败到一定次数属于「硬故障」，走原有告警路径，不在退化里重复报。"""
    records = [_record(f"t{i}", p=(False, "boom")) for i in range(6)]
    assert trend.degraded_platforms(trend.platform_trends(records)) == []


def test_degraded_sorted_worst_first():
    records = [
        _record("t1", bad=(False, "e"), bad2=(False, "e"), worse=(False, "e")),
        _record("t2", bad=(True, ""), bad2=(False, "e"), worse=(False, "e")),
        _record("t3", bad=(True, ""), bad2=(True, ""), worse=(False, "e")),
        _record("t4", bad=(True, ""), bad2=(True, ""), worse=(False, "e")),
        _record("t5", bad=(True, ""), bad2=(True, ""), worse=(False, "e")),
    ]
    degraded = trend.degraded_platforms(trend.platform_trends(records))
    rates = [d["rate"] for d in degraded]
    assert rates == sorted(rates), "应按成功率升序（最差在前）"


# ------------------------------------------------------------------ 报告与文案
def test_build_trend_section_shape():
    records = [_record(f"t{i}", p=(True, "")) for i in range(6)]
    trends = trend.platform_trends(records)
    degraded = trend.degraded_platforms(trends)
    section = trend.build_trend_section(trends, degraded)
    assert section["window"] == trend.TREND_WINDOW
    assert section["evaluated"] == 1
    assert section["healthy"] == 1
    assert section["degraded"] == []


def test_render_trend_lines_empty_when_healthy():
    report = {"trend": {"window": 20, "degraded": []}}
    assert trend.render_trend_lines(report) == []


def test_render_trend_lines_mentions_rate_and_reason():
    report = {
        "trend": {
            "window": 20,
            "degraded": [
                {
                    "name": "kuaishou-video",
                    "platform": "kuaishou",
                    "rate": 0.5,
                    "ok": 3,
                    "samples": 6,
                    "streak_fail": 1,
                    "details": ["HTTP 403"],
                }
            ],
        }
    }
    lines = trend.render_trend_lines(report)
    text = "\n".join(lines)
    assert "50%" in text
    assert "kuaishou-video" in text
    assert "HTTP 403" in text


def test_render_trend_lines_respects_limit():
    report = {
        "trend": {
            "window": 20,
            "degraded": [
                {
                    "name": f"p{i}",
                    "platform": "p",
                    "rate": 0.1,
                    "ok": 0,
                    "samples": 10,
                    "streak_fail": 1,
                    "details": [],
                }
                for i in range(10)
            ],
        }
    }
    # limit 控制"列出几个平台"，不控制行数（每个平台的第二行明细另算）
    lines = trend.render_trend_lines(report, limit=3)
    platform_lines = [ln for ln in lines if ln.startswith("·")]
    assert len(platform_lines) == 3


# ------------------------------------------------------------ 退化告警去重（静默）
def _degraded(name: str, *, rate: float = 0.7, samples: int = 20) -> dict:
    return {
        "name": name,
        "platform": name.split("-")[0],
        "rate": rate,
        "ok": int(rate * samples),
        "samples": samples,
        "streak_fail": 1,
        "details": [],
    }


def test_split_degraded_alert_fires_on_first_occurrence():
    """首次退化要告警，并落一份带 last_alert_ts 的快照。"""
    degraded = [_degraded("huya-video")]
    to_alert, state = trend.split_degraded_alerts(degraded, {}, now="2026-09-19T00:00:00+00:00")
    assert [item["name"] for item in to_alert] == ["huya-video"]
    assert state["huya-video"]["last_alert_ts"].startswith("2026-09-19T00:00:00")


def test_split_degraded_alert_silences_repeat_within_window():
    """同一状态期内重复判定要静默 —— 这是「每半小时重发」的根因修复。"""
    prev = {"huya-video": {"rate": 0.7, "samples": 20, "last_alert_ts": "2026-09-19T00:00:00+00:00"}}
    degraded = [_degraded("huya-video", rate=0.75)]
    # 6 小时静默期内：不告警，且 last_alert_ts 保持原值（不刷新计时）
    to_alert, state = trend.split_degraded_alerts(
        degraded, prev, now="2026-09-19T03:00:00+00:00", silence_minutes=360
    )
    assert to_alert == []
    assert state["huya-video"]["last_alert_ts"] == "2026-09-19T00:00:00+00:00"
    assert state["huya-video"]["rate"] == 0.75  # 快照仍更新为最新值


def test_split_degraded_alert_re_alerts_after_window():
    """静默期一过、平台仍在退化 → 再提醒一次，避免长期无人知晓。"""
    prev = {"huya-video": {"rate": 0.7, "samples": 20, "last_alert_ts": "2026-09-19T00:00:00+00:00"}}
    to_alert, state = trend.split_degraded_alerts(
        [_degraded("huya-video")], prev, now="2026-09-19T07:00:00+00:00", silence_minutes=360
    )
    assert [item["name"] for item in to_alert] == ["huya-video"]
    assert state["huya-video"]["last_alert_ts"].startswith("2026-09-19T07:00:00")


def test_split_degraded_alert_recovers_then_degrades_again():
    """恢复后再变差 → 视为新状态期，立即告警（不因旧快照被长期静默）。"""
    # 恢复那一轮：degraded 为空 → 快照里不再有该平台
    to_alert, state = trend.split_degraded_alerts([], {"huya-video": {}}, now="2026-09-19T05:00:00+00:00")
    assert to_alert == [] and state == {}
    # 再次退化且 prev 已无该平台 → 首次，告警
    to_alert, state = trend.split_degraded_alerts(
        [_degraded("huya-video")], state, now="2026-09-19T05:30:00+00:00"
    )
    assert [item["name"] for item in to_alert] == ["huya-video"]


def test_split_degraded_alert_zero_silence_disables_dedupe():
    """silence_minutes=0 → 关闭静默，退化为改造前「每轮都报」的行为。"""
    prev = {"huya-video": {"rate": 0.7, "samples": 20, "last_alert_ts": "2026-09-19T00:00:00+00:00"}}
    to_alert, _ = trend.split_degraded_alerts(
        [_degraded("huya-video")], prev, now="2026-09-19T00:30:00+00:00", silence_minutes=0
    )
    assert [item["name"] for item in to_alert] == ["huya-video"]


def test_split_degraded_alert_drops_recovered_platform():
    """平台不再退化 → 从快照中移除，避免状态期无限延续。"""
    prev = {
        "huya-video": {"rate": 0.7, "samples": 20, "last_alert_ts": "2026-09-19T00:00:00+00:00"},
        "douyin-video": {"rate": 0.5, "samples": 20, "last_alert_ts": "2026-09-19T00:00:00+00:00"},
    }
    to_alert, state = trend.split_degraded_alerts(
        [_degraded("douyin-video")], prev, now="2026-09-19T01:00:00+00:00"
    )
    assert list(state) == ["douyin-video"]
    assert to_alert == []  # douyin 仍在静默期


def test_split_degraded_alert_tolerates_corrupt_state():
    """坏快照（时间戳无法解析）不能让巡检崩掉，按首次处理即可。"""
    to_alert, state = trend.split_degraded_alerts(
        [_degraded("huya-video")], {"huya-video": {"last_alert_ts": "not-a-date"}},
        now="2026-09-19T00:00:00+00:00",
    )
    assert [item["name"] for item in to_alert] == ["huya-video"]
    assert state["huya-video"]["last_alert_ts"].startswith("2026-09-19T00:00:00")


def test_trend_section_counts_are_exhaustive_and_disjoint():
    """healthy + degraded + hardDown 必须等于 evaluated，口径不重不漏。

    这三类分别对应「达标 / 间歇退化（本模块新增告警）/ 连续硬故障（原有告警）」，
    报告读者据此判断趋势全貌，算错会误导排查方向。
    """
    records = []
    # healthy：全成功
    records += [_record(f"h{i}", healthy_p=(True, "")) for i in range(6)]
    # degraded：间歇失败，最近成功
    records += [_record(f"d{i}", degraded_p=(False, "403")) for i in range(4)]
    records += [_record(f"dok{i}", degraded_p=(True, "")) for i in range(1)]
    records += [_record(f"dok2{i}", degraded_p=(True, "")) for i in range(1)]
    # hard down：最近连续失败
    records += [_record(f"x{i}", hard_p=(False, "boom")) for i in range(6)]

    trends = trend.platform_trends(records)
    degraded = trend.degraded_platforms(trends)
    section = trend.build_trend_section(trends, degraded)

    assert section["degraded"], "应检出间歇退化平台"
    assert section["hardDown"] == 1
    assert section["healthy"] == 1
    assert (
        section["healthy"] + section["hardDown"] + len(section["degraded"])
        == section["evaluated"]
    ), "三类计数之和必须等于 evaluated"


# ================================================================== 多样本聚合
def _results(**items) -> dict:
    """构造条目级巡检结果：{name: (ok, platform, detail)}。"""
    return {name: {"ok": ok, "platform": platform, "detail": detail}
            for name, (ok, platform, detail) in items.items()}


def test_group_by_platform_aggregates_entries():
    results = _results(
        k1=(True, "kuaishou", ""),
        k2=(False, "kuaishou", "403"),
        d1=(True, "douyin", ""),
    )
    groups = trend.group_by_platform(results)
    assert groups["kuaishou"]["samples"] == 2
    assert groups["kuaishou"]["ok"] == 1
    assert groups["kuaishou"]["fail"] == 1
    assert groups["kuaishou"]["failed_names"] == ["k2"]
    assert groups["douyin"]["fail"] == 0


def test_partial_failure_is_classified_as_stale_link():
    """多条样本里只挂一条 → 判定为链接过期，不是平台故障。"""
    results = _results(
        k1=(True, "kuaishou", ""),
        k2=(True, "kuaishou", ""),
        k3=(False, "kuaishou", "HTTP 404"),
    )
    cls = trend.classify_failures(results)
    assert [e["platform"] for e in cls["partial"]] == ["kuaishou"]
    assert cls["allDown"] == []
    assert cls["partial"][0]["failed_names"] == ["k3"]


def test_all_samples_down_is_classified_as_platform_fault():
    """全部样本都挂 → 判定为解析器 / 平台问题（需处理）。"""
    results = _results(
        k1=(False, "kuaishou", "403"),
        k2=(False, "kuaishou", "403"),
    )
    cls = trend.classify_failures(results)
    assert [e["platform"] for e in cls["allDown"]] == ["kuaishou"]
    assert cls["partial"] == []


def test_single_sample_failure_is_conservative():
    """只有 1 条样本且失败时无法区分，保守归为需处理（allDown）。"""
    results = _results(k1=(False, "kuaishou", "boom"))
    cls = trend.classify_failures(results)
    assert [e["platform"] for e in cls["allDown"]] == ["kuaishou"]


def test_healthy_platforms_listed():
    results = _results(a=(True, "p1", ""), b=(True, "p2", ""))
    cls = trend.classify_failures(results)
    assert {e["platform"] for e in cls["healthy"]} == {"p1", "p2"}
    assert cls["allDown"] == [] and cls["partial"] == []


def test_classify_handles_mixed_platforms():
    results = _results(
        k1=(True, "kuaishou", ""), k2=(False, "kuaishou", "HTTP 404"),
        d1=(False, "douyin", "HTTP 403"), d2=(False, "douyin", "HTTP 403"),
        b1=(True, "bilibili", ""),
    )
    cls = trend.classify_failures(results)
    assert [e["platform"] for e in cls["partial"]] == ["kuaishou"]
    assert [e["platform"] for e in cls["allDown"]] == ["douyin"]
    assert [e["platform"] for e in cls["healthy"]] == ["bilibili"]
    assert cls["flaky"] == []


def test_transient_failure_is_classified_as_flaky_not_stale_link():
    """超时 / 连接层失败 → 上游瞬时抖动，不该提示换链接。"""
    results = _results(
        k1=(True, "kuaishou", ""),
        k2=(False, "kuaishou", "媒体代理异常 HTTP 0 bytes=0"),
        h1=(True, "huya", ""),
        h2=(False, "huya", "解析失败 HTTP 504 PARSE_TIMEOUT 解析超时，请稍后重试"),
    )
    cls = trend.classify_failures(results)
    assert {e["platform"] for e in cls["flaky"]} == {"kuaishou", "huya"}
    assert cls["partial"] == []
    assert cls["allDown"] == []


def test_group_with_both_4xx_and_timeout_is_partial():
    """同一平台既有 4xx 又有超时 → 按确定性信号归为链接过期（保守）。"""
    results = _results(
        k1=(True, "kuaishou", ""),
        k2=(False, "kuaishou", "HTTP 410 作品已删除"),
        k3=(False, "kuaishou", "解析失败 HTTP 504 PARSE_TIMEOUT 解析超时"),
    )
    cls = trend.classify_failures(results)
    assert [e["platform"] for e in cls["partial"]] == ["kuaishou"]
    assert cls["flaky"] == []


def test_render_multisample_lines_empty_without_partial():
    assert trend.render_multisample_lines({"sampleCoverage": {}}) == []
    assert trend.render_multisample_lines(
        {"sampleCoverage": {"partial": []}}
    ) == []
    assert trend.render_multisample_lines(
        {"sampleCoverage": {"flaky": []}}
    ) == []


def test_render_multisample_lines_separates_flaky_from_partial():
    report = {
        "sampleCoverage": {
            "partial": [
                {
                    "platform": "kuaishou",
                    "samples": 3,
                    "ok": 2,
                    "fail": 1,
                    "failed_names": ["kuaishou-old"],
                    "details": ["HTTP 404"],
                }
            ],
            "flaky": [
                {
                    "platform": "huya",
                    "samples": 2,
                    "ok": 1,
                    "fail": 1,
                    "failed_names": ["huya-video"],
                    "details": ["解析失败 HTTP 504 PARSE_TIMEOUT 解析超时"],
                }
            ],
        }
    }
    text = "\n".join(trend.render_multisample_lines(report))
    assert "kuaishou" in text
    assert "kuaishou-old" in text
    assert "2/3" in text
    assert "建议更换" in text
    assert "huya" in text
    assert "上游瞬时抖动" in text and "无需换链接" in text


# ================================================================== 巡检清单
# ops/healthcheck.py 的清单加载（公共样本 + 私有覆盖合并）。
def _load_healthcheck():
    spec = importlib.util.spec_from_file_location(
        "healthcheck_mod", _OPS / "healthcheck.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_links_ignores_comment_keys(tmp_path):
    hc = _load_healthcheck()
    path = tmp_path / "l.json"
    path.write_text(
        json.dumps({"_comment": "说明", "a": {"platform": "p", "url": "http://x"}}),
        "utf-8",
    )
    links = hc._load_links(path)
    assert list(links) == ["a"]


def test_load_links_optional_missing_returns_empty(tmp_path):
    """私有清单是可选的，不存在时不该报错。"""
    hc = _load_healthcheck()
    assert hc._load_links(tmp_path / "nope.json", optional=True) == {}


def test_load_links_corrupt_raises_systemexit(tmp_path):
    """清单写坏必须硬失败 —— 静默跳过等于巡检形同虚设。"""
    hc = _load_healthcheck()
    path = tmp_path / "bad.json"
    path.write_text("{not json", "utf-8")
    with pytest.raises(SystemExit):
        hc._load_links(path)


def test_private_links_override_public_on_name_clash(tmp_path):
    """部署者私有清单里同名条目应覆盖公共样本（便于替换失效链接）。"""
    hc = _load_healthcheck()
    public = tmp_path / "pub.json"
    private = tmp_path / "priv.json"
    public.write_text(
        json.dumps({"k": {"platform": "p", "url": "http://public"}}), "utf-8"
    )
    private.write_text(
        json.dumps({"k": {"platform": "p", "url": "http://private"}}), "utf-8"
    )
    links = hc._load_links(public)
    links.update(hc._load_links(private, optional=True))
    assert links["k"]["url"] == "http://private"
