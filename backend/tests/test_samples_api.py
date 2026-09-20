"""成功样本池的 API 与解析链路接入测试。

覆盖两层：
1. ``GET /api/stats/samples`` / ``/api/stats/samples/export`` 的可见性与导出格式
   （与其他统计接口一致，只对未经代理直连的本机开放）
2. ``parse_one`` 成功后确实把链接沉淀进样本池 —— 且收的是**用户粘贴的原始链接**
   经凭证剥离后的版本
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import parsing as parsing_module
from app import samples as samples_module
from app.main import app
from app.parsers.kuaishou import ParsedWork

client = TestClient(app)


def _sync(coro):
    """独立事件循环驱动，避免复用已被其他测试关闭的 loop。"""
    import asyncio

    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _local_host(monkeypatch):
    """让 TestClient 的 client.host（testclient）被视作本机。"""
    monkeypatch.setenv("STATS_LOCAL_HOSTS", "testclient")


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """换掉全局样本池，避免污染真实 data/ 目录。"""
    store = samples_module.SampleStore(tmp_path / "samples.jsonl", enabled=True)
    monkeypatch.setattr(samples_module, "SAMPLES", store)
    monkeypatch.setattr(main_module, "samples", samples_module, raising=False)
    return store


# ------------------------------------------------------------------ API
def test_samples_api_hidden_for_proxied_requests(isolated_store):
    """经代理的请求（带 x-real-ip）应拿到 404，不泄露样本。"""
    resp = client.get("/api/stats/samples", headers={"x-real-ip": "1.2.3.4"})
    assert resp.status_code == 404


def test_samples_api_visible_locally(isolated_store):
    isolated_store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    resp = client.get("/api/stats/samples")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["report"]["total"] == 1
    assert body["candidates"]["douyin"][0]["url"] == "https://v.douyin.com/abc/"


def test_samples_export_hidden_for_proxied_requests(isolated_store):
    resp = client.get("/api/stats/samples/export", headers={"x-forwarded-for": "1.2.3.4"})
    assert resp.status_code == 404


def test_samples_export_text_is_loadable_json(isolated_store):
    import json

    isolated_store.record(platform="douyin", url="https://v.douyin.com/abc/", kind="video")
    resp = client.get("/api/stats/samples/export")
    assert resp.status_code == 200
    parsed = json.loads(resp.text)
    assert parsed["douyin-auto1"]["url"] == "https://v.douyin.com/abc/"


def test_samples_export_json_format(isolated_store):
    isolated_store.record(platform="kuaishou", url="https://v.kuaishou.com/a", kind="video")
    resp = client.get("/api/stats/samples/export?fmt=json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["links"]["kuaishou-auto1"]["platform"] == "kuaishou"


# ------------------------------------------------------------------ 解析链路接入
async def _run_parse(url, source="web"):
    """parse_one 是 async，测试里包一层同步驱动。"""
    return await parsing_module.parse_one(url, source=source, client_ip="127.0.0.1")


def test_parse_one_records_successful_link(tmp_path, monkeypatch):
    """解析成功后，用户粘贴的链接应被沉淀进样本池。"""
    store = samples_module.SampleStore(tmp_path / "samples.jsonl", enabled=True)
    monkeypatch.setattr(samples_module, "SAMPLES", store)
    monkeypatch.setattr(parsing_module, "samples", samples_module)
    # 清缓存，避免命中旧结果绕过记录
    monkeypatch.setattr(parsing_module, "_CACHE", {})

    work = ParsedWork(
        type="video",
        title="t",
        author="a",
        video_url="https://cdn.example.com/a.mp4",
    )

    async def fake_parse(url, **kwargs):
        return work

    monkeypatch.setattr(parsing_module, "_parsers", lambda: {"douyin": fake_parse})
    monkeypatch.setattr(
        parsing_module,
        "extract_share_url",
        lambda text: ("douyin", "https://v.douyin.com/abc/"),
    )

    payload = _sync(_run_parse("https://v.douyin.com/abc/"))
    assert payload.get("ok") is not False

    items = store.items()
    assert len(items) == 1
    assert items[0]["platform"] == "douyin"
    assert items[0]["url"] == "https://v.douyin.com/abc/"
    assert items[0]["kind"] == "video"


def test_parse_one_records_url_verbatim(tmp_path, monkeypatch):
    """用户粘贴的链接原样入库，不做参数剥离 —— 剥离会让样本无法复现解析。"""
    store = samples_module.SampleStore(tmp_path / "samples.jsonl", enabled=True)
    monkeypatch.setattr(samples_module, "SAMPLES", store)
    monkeypatch.setattr(parsing_module, "samples", samples_module)
    monkeypatch.setattr(parsing_module, "_CACHE", {})

    work = ParsedWork(
        type="video",
        title="t",
        author="a",
        video_url="https://cdn.example.com/a.mp4",
    )

    async def fake_parse(url, **kwargs):
        return work

    monkeypatch.setattr(parsing_module, "_parsers", lambda: {"douyin": fake_parse})
    url = "https://www.douyin.com/video/123?u_code=USERCODE&mid=MID&share_sign=SIG"
    monkeypatch.setattr(parsing_module, "extract_share_url", lambda text: ("douyin", url))

    _sync(_run_parse(url))

    stored = store.items()[0]["url"]
    assert stored == url


def test_parse_one_cache_hit_still_counts(tmp_path, monkeypatch):
    """缓存命中（重复解析同一链接）也要累加命中次数。

    巡检候选的可靠性来自「反复成功」：命中越多越可信。因此缓存分支也要记，
    否则第二次起就不再计数。
    """
    store = samples_module.SampleStore(tmp_path / "s.jsonl", enabled=True)
    monkeypatch.setattr(samples_module, "SAMPLES", store)
    monkeypatch.setattr(parsing_module, "samples", samples_module)
    monkeypatch.setattr(parsing_module, "_CACHE", {})

    work = ParsedWork(type="video", title="t", author="a",
                      video_url="https://cdn.example.com/a.mp4")

    async def fake_parse(url, **kwargs):
        return work

    monkeypatch.setattr(parsing_module, "_parsers", lambda: {"douyin": fake_parse})
    monkeypatch.setattr(
        parsing_module, "extract_share_url",
        lambda text: ("douyin", "https://v.douyin.com/abc/"),
    )

    _sync(_run_parse("https://v.douyin.com/abc/"))
    _sync(_run_parse("https://v.douyin.com/abc/"))  # 第二次走缓存
    assert store.size() == 1
    assert store.items()[0]["count"] == 2
