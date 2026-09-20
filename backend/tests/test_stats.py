"""解析调用统计的测试。

要点：
- 耗时指标只统计「未命中缓存」的请求（缓存命中的耗时没有参考价值）
- `/api/stats` 只对「未经代理直连」的本机开放；带 X-Real-IP 一律 404
- 巡检流量通过 X-VD-Source: healthcheck 与真实用户流量分开统计
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import stats as stats_module
from app.errors import WORK_UNAVAILABLE
from app.main import app
from app.parsers.kuaishou import ParsedWork

client = TestClient(app)
UNSUPPORTED = {"input": "https://www.youtube.com/watch?v=1"}


@pytest.fixture
def local(monkeypatch):
    """让 TestClient 的 client.host（testclient）被视作本机。"""
    monkeypatch.setenv("STATS_LOCAL_HOSTS", "testclient")
    return monkeypatch


# ---------------------------------------------------------------- 聚合口径
def test_bucket_metrics_exclude_cache_from_latency():
    bucket = stats_module.Bucket()
    bucket.record(ok=True, ms=1000, cached=False, error=None, source="web")
    bucket.record(ok=False, ms=3000, cached=False, error="PARSE_FAILED", source="web")
    bucket.record(ok=True, ms=5, cached=True, error=None, source="web")

    metrics = bucket.metrics()
    assert metrics["total"] == 3
    assert (metrics["ok"], metrics["fail"]) == (2, 1)
    assert metrics["cached"] == 1
    assert metrics["parsed"] == 2  # 缓存命中不计入解析耗时
    assert metrics["successRate"] == round(2 / 3, 4)
    assert metrics["avgMs"] == 2000  # (1000 + 3000) / 2，不含缓存那次的 5ms
    assert metrics["maxMs"] == 3000
    assert metrics["errors"] == {"PARSE_FAILED": 1}
    assert metrics["bySource"] == {"web": 3}


def test_p95_uses_sorted_samples():
    bucket = stats_module.Bucket()
    for ms in range(1, 101):
        bucket.record(ok=True, ms=ms, cached=False, error=None, source="web")
    # 100 个样本，下标 int(100 * 0.95) = 95 → 第 96 小的值
    assert bucket.metrics()["p95Ms"] == 96


def test_reservoir_sampling_caps_samples():
    bucket = stats_module.Bucket()
    for ms in range(stats_module.SAMPLE_CAP * 2):
        bucket.record(ok=True, ms=ms, cached=False, error=None, source="web")
    assert len(bucket.samples) == stats_module.SAMPLE_CAP
    assert bucket.metrics()["total"] == stats_module.SAMPLE_CAP * 2


def test_report_separates_rejected_from_platforms(tmp_path):
    store = stats_module.Stats(tmp_path / "s.json")
    store.record("kuaishou", ok=True, ms=100, source="web")
    store.record("kuaishou", ok=False, ms=200, error="PARSE_FAILED", source="web")
    store.record("douyin", ok=True, ms=300, source="healthcheck")
    store.record(stats_module.REJECTED, ok=False, ms=1, error="UNSUPPORTED_PLATFORM", source="web")

    report = store.report()
    assert set(report["platforms"]) == {"kuaishou", "douyin"}
    assert report["platforms"]["kuaishou"]["total"] == 2
    assert report["overall"]["total"] == 3  # 非法输入不计入 overall
    assert report["rejected"]["total"] == 1
    assert report["rejected"]["errors"] == {"UNSUPPORTED_PLATFORM": 1}


# ---------------------------------------------------------------- 持久化
def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "stats.json"
    store = stats_module.Stats(path)
    store.record("kuaishou", ok=True, ms=120, source="web")
    store.record("kuaishou", ok=False, ms=80, error="PARSE_FAILED", source="web")
    store.flush()
    assert path.exists()

    restored = stats_module.Stats(path).report()["platforms"]["kuaishou"]
    assert restored["total"] == 2
    assert restored["ok"] == 1
    assert restored["avgMs"] == 100
    assert restored["p95Ms"] == 120


def test_flush_is_throttled_but_close_forces_it(tmp_path):
    path = tmp_path / "stats.json"
    store = stats_module.Stats(path, flush_sec=3600)
    store.record("kuaishou", ok=True, ms=1, source="web")
    assert not path.exists()  # 未到落盘间隔，不写盘
    store.close()
    assert path.exists()  # 退出时强制落盘


def test_corrupt_store_is_ignored(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("{ 这不是 JSON", "utf-8")
    store = stats_module.Stats(path)  # 不应抛异常
    assert store.report()["platforms"] == {}


def test_retention_prunes_days_outside_window(tmp_path):
    store = stats_module.Stats(tmp_path / "stats.json", retention_days=2)
    old = stats_module.Bucket()
    old.record(ok=True, ms=1, cached=False, error=None, source="web")
    store._days["2020-01-01"] = {"kuaishou": old}

    store.record("douyin", ok=True, ms=5, source="web")  # 触发清理
    assert "2020-01-01" not in store._days
    assert set(store.report()["platforms"]) == {"douyin"}


# ---------------------------------------------------------------- 接口鉴权
def test_stats_allows_local(local):
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_stats_hidden_from_proxied_requests(local):
    response = client.get("/api/stats", headers={"x-real-ip": "1.2.3.4"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"

    # 只带 X-Forwarded-For 同样视为来自代理
    response = client.get("/api/stats", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert response.status_code == 404


def test_stats_token_works_from_anywhere(local, monkeypatch):
    monkeypatch.setenv("STATS_TOKEN", "s3cret")
    assert client.get("/api/stats", headers={"x-real-ip": "1.2.3.4"}).status_code == 404
    response = client.get("/api/stats", headers={"x-real-ip": "1.2.3.4", "x-stats-token": "s3cret"})
    assert response.status_code == 200


def test_stats_text_format(local):
    stats_module.STATS.record("kuaishou", ok=True, ms=10, source="web")
    response = client.get("/api/stats?fmt=text")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "全部平台" in response.text
    assert "kuaishou" in response.text


# ---------------------------------------------------------------- 埋点
def test_rejected_inputs_are_counted(local):
    assert client.post("/api/parse", json=UNSUPPORTED).status_code == 400
    report = client.get("/api/stats").json()
    assert report["rejected"]["total"] == 1
    assert report["rejected"]["errors"] == {"UNSUPPORTED_PLATFORM": 1}
    assert report["platforms"] == {}


def test_platform_failure_is_counted(local, monkeypatch):
    monkeypatch.setattr(main_module, "extract_share_url", lambda text: ("douyin", "https://v.douyin.com/stats-fail"))

    async def boom(url, timeout=None, client=None):
        raise WORK_UNAVAILABLE

    monkeypatch.setattr(main_module, "parse_douyin", boom)
    assert client.post("/api/parse", json={"input": "x"}).status_code == 422

    metrics = client.get("/api/stats").json()["platforms"]["douyin"]
    assert metrics["total"] == 1
    assert metrics["fail"] == 1
    assert metrics["successRate"] == 0.0
    assert metrics["errors"] == {"WORK_UNAVAILABLE": 1}


def test_success_is_counted_and_tagged_with_source(local, monkeypatch):
    monkeypatch.setattr(main_module, "extract_share_url", lambda text: ("kuaishou", "https://v.kuaishou.com/stats-ok"))

    async def fake_parse(url, timeout=None, client=None):
        return ParsedWork(type="video", title="标题", author="作者", video_url="https://cdn.example/v.mp4")

    monkeypatch.setattr(main_module, "parse_kuaishou", fake_parse)
    response = client.post("/api/parse", json={"input": "x"}, headers={"x-vd-source": "healthcheck"})
    assert response.status_code == 200

    report = client.get("/api/stats").json()
    metrics = report["platforms"]["kuaishou"]
    assert metrics["total"] == 1 and metrics["ok"] == 1
    assert metrics["successRate"] == 1.0
    assert metrics["bySource"] == {"healthcheck": 1}
    assert report["overall"]["bySource"] == {"healthcheck": 1}


def test_cache_hit_is_marked_as_cached(local, monkeypatch):
    monkeypatch.setattr(
        main_module, "extract_share_url", lambda text: ("kuaishou", "https://v.kuaishou.com/stats-cached")
    )

    async def fake_parse(url, timeout=None, client=None):
        return ParsedWork(type="video", title="t", author="a", video_url="https://cdn.example/v.mp4")

    monkeypatch.setattr(main_module, "parse_kuaishou", fake_parse)
    assert client.post("/api/parse", json={"input": "x"}).status_code == 200
    assert client.post("/api/parse", json={"input": "x"}).status_code == 200  # 命中 60s 解析缓存

    metrics = client.get("/api/stats").json()["platforms"]["kuaishou"]
    assert metrics["total"] == 2
    assert metrics["cached"] == 1
    assert metrics["parsed"] == 1  # 只有一次真的去抓了平台
