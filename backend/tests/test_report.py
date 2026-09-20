"""一键诊断报告（/api/stats/report）的测试。

要点：
- 把「统计」与「失败样本」合成一份文本：含统计表、待修样本、给模型的提问
- 与 /api/stats、/api/stats/failures 一致：只对「未经代理直连」的本机开放
- days / limit / kind 参数生效
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import stats as stats_module
from app.main import app

client = TestClient(app)


@pytest.fixture
def local(monkeypatch):
    """让 TestClient 的 client.host（testclient）被视作本机。"""
    monkeypatch.setenv("STATS_LOCAL_HOSTS", "testclient")
    return monkeypatch


def _seed_stats() -> None:
    """造一点统计：kuaishou 全成功、bilibili 有一条失败。"""
    day = stats_module.datetime.now().strftime("%Y-%m-%d")
    buckets = stats_module.STATS._days.setdefault(day, {})
    ok = stats_module.Bucket()
    ok.record(ok=True, ms=900, cached=False, error=None, source="web")
    buckets["kuaishou"] = ok
    bad = stats_module.Bucket()
    bad.record(ok=False, ms=1500, cached=False, error="PARSE_FAILED", source="web")
    buckets["bilibili"] = bad
    stats_module.STATS._dirty = True


def _seed_failure() -> None:
    from app import failures

    failures.SAMPLES.record(
        url="https://www.bilibili.com/video/BV1nope00000",
        input_text="https://www.bilibili.com/video/BV1nope00000",
        platform="bilibili",
        code="PARSE_FAILED",
        message="解析失败，请稍后重试",
        ms=1500,
        source="web",
    )


# ---------------------------------------------------------------- 内容
def test_report_merges_stats_and_failures(local):
    _seed_stats()
    _seed_failure()
    r = client.get("/api/stats/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert "video-dewatermark 解析诊断报告" in text
    # 统计部分：平台名与成功率都在
    assert "kuaishou" in text and "bilibili" in text
    assert "全部平台" in text
    # 失败样本部分：链接与定性都在
    assert "BV1nope00000" in text
    assert "kind=parser_bug" in text
    # 给模型的收尾提问
    assert "请回答" in text


def test_report_without_samples(local):
    """没有任何失败样本时也要能出报告（不报错、给出空态）。"""
    _seed_stats()
    r = client.get("/api/stats/report")
    assert r.status_code == 200
    assert "暂无失败样本" in r.text


# ---------------------------------------------------------------- 参数
def test_report_limit_and_kind(local):
    _seed_stats()
    _seed_failure()
    r = client.get("/api/stats/report?kind=input")
    assert r.status_code == 200
    # 只保留 input 定性，parser_bug 那条不应出现
    assert "BV1nope00000" not in r.text
    assert "暂无失败样本" in r.text

    r2 = client.get("/api/stats/report?kind=parser_bug&limit=1")
    assert r2.status_code == 200
    assert "BV1nope00000" in r2.text


def test_report_days_window(local):
    _seed_stats()
    # 窗口限定为 0/1 天不影响结构，仅窗口描述变化
    r = client.get("/api/stats/report?days=1")
    assert r.status_code == 200
    assert "video-dewatermark 解析诊断报告" in r.text


# ---------------------------------------------------------------- 安全
def test_report_rejected_from_proxy(local):
    """带 X-Real-IP 的请求视为来自代理，一律 404（与 /api/stats 同口径）。"""
    r = client.get("/api/stats/report", headers={"X-Real-IP": "1.2.3.4"})
    assert r.status_code == 404


# ---------------------------------------------------------------- 渲染单测
def test_render_health_report_contains_sections():
    stats_report = {
        "checkedAt": "2026-01-01T00:00:00",
        "retentionDays": 7,
        "overall": {
            "total": 3, "ok": 2, "fail": 1, "cached": 1, "successRate": round(2 / 3, 4),
            "avgMs": 1200, "p95Ms": 1500, "maxMs": 1500, "bySource": {}, "errors": {},
        },
        "platforms": {
            "kuaishou": {
                "total": 3, "ok": 2, "fail": 1, "cached": 1, "successRate": round(2 / 3, 4),
                "avgMs": 1200, "p95Ms": 1500, "maxMs": 1500, "bySource": {}, "errors": {},
            },
        },
        "rejected": {"total": 2, "errors": {"INVALID_LINK": 2}},
    }
    failures_report = {
        "checkedAt": "2026-01-01T00:00:00",
        "total": 1,
        "returned": 1,
        "retentionDays": 7,
        "maxRecords": 300,
        "summary": {"byKind": {"input": 1}, "byPlatform": {"_rejected": 1}, "totalOccurrences": 1},
        "items": [
            {
                "kind": "input", "platform": "_rejected", "count": 1,
                "lastSeen": "2026-01-01T00:00:00", "lastCode": "INVALID_LINK",
                "lastMessage": "未识别到有效链接", "lastMs": 0,
                "url": "", "input": "点击链接直接打开",
            }
        ],
    }
    out = stats_module.render_health_report(stats_report, failures_report)
    assert "解析诊断报告" in out
    assert "非法/不支持的输入 2 次" in out
    assert "未识别到有效链接" in out
    assert out.endswith("\n")
