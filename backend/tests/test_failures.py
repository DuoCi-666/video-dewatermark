"""解析失败样本（failures.jsonl）的测试。

要点：
- 同一条链接反复失败只累加 count，不重复写行（否则一条失效链接会刷满整个文件）
- kind 定性决定复查优先级：input > parser_bug > risk > gone
- 输入过短/空等噪声错误码不记录
- 条数 / 天数 / 字节三重封顶，裁剪时优先丢 gone 与最久未出现的
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import failures as failures_module
from app import main as main_module
from app.errors import WORK_UNAVAILABLE
from app.main import app

client = TestClient(app)
NOISE_RESPONSE = {"input": "https://www.youtube.com/watch?v=1"}


@pytest.fixture
def local(monkeypatch):
    """让 TestClient 的 client.host（testclient）被视作本机。"""
    monkeypatch.setenv("STATS_LOCAL_HOSTS", "testclient")
    return monkeypatch


def _fail(
    store,
    *,
    url: str = "https://v.douyin.com/x",
    code: str = "PARSE_FAILED",
    platform: str = "douyin",
    text: str = "看看这个 https://v.douyin.com/x",
) -> bool:
    return store.record(
        url=url,
        input_text=text,
        platform=platform,
        code=code,
        message="解析失败，请稍后重试",
        ms=100,
        source="web",
        client_ip="1.2.3.4",
    )


# ---------------------------------------------------------------- 记录与去重
def test_records_expected_fields(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    assert _fail(store) is True

    item = store.report()["items"][0]
    assert item["platform"] == "douyin"
    assert item["url"] == "https://v.douyin.com/x"
    assert item["kind"] == "parser_bug"
    assert item["count"] == 1
    assert item["lastCode"] == "PARSE_FAILED"
    assert item["sources"] == {"web": 1}
    assert item["lastIp"] == "1.2.3.4"
    assert item["firstSeen"] and item["lastSeen"]


def test_same_url_is_deduplicated(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    for _ in range(7):
        _fail(store)

    assert store.size() == 1  # 只占一行
    assert store.report()["items"][0]["count"] == 7
    assert store.report()["summary"]["totalOccurrences"] == 7


def test_different_url_or_platform_are_separate(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, url="https://a/1")
    _fail(store, url="https://a/2")
    _fail(store, url="https://a/1", platform="kuaishou")
    assert store.size() == 3


def test_noise_codes_are_not_recorded(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    assert _fail(store, code="EMPTY_INPUT") is False
    assert _fail(store, code="INVALID_INPUT") is False
    assert store.size() == 0


def test_kinds_accumulate_and_latest_wins(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, code="PARSE_FAILED")
    _fail(store, code="WORK_UNAVAILABLE")

    item = store.report()["items"][0]
    assert item["kind"] == "gone"  # 以最近一次的定性为准，便于按 kind 过滤
    assert item["kinds"] == {"parser_bug": 1, "gone": 1}


def test_input_is_clipped(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, text="x" * 1000)
    assert len(store.report()["items"][0]["input"]) == failures_module.INPUT_MAX


def test_input_becomes_the_key_when_link_is_unknown(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, url="", code="INVALID_LINK", platform="_rejected", text="纯文本，没有链接")

    item = store.report()["items"][0]
    assert item["url"] == ""
    assert item["kind"] == "input"
    assert item["input"] == "纯文本，没有链接"


# ---------------------------------------------------------------- 容量与排序
def test_max_records_drops_oldest(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl", max_records=2)
    for index in range(3):
        _fail(store, url=f"https://a/{index}")

    report = store.report()
    assert report["total"] == 2
    urls = {item["url"] for item in report["items"]}
    assert urls == {"https://a/1", "https://a/2"}  # 最旧的 a/0 被丢弃


def test_max_bytes_stops_file_growth(tmp_path):
    path = tmp_path / "f.jsonl"
    store = failures_module.Failures(path, max_records=1000, max_bytes=4096)
    for index in range(60):
        _fail(store, url=f"https://example.com/{index}", text="x" * 290)

    store.flush()
    assert path.stat().st_size <= 4096
    assert store.size() >= 1  # 至少保留一条，不会清空


def test_expired_records_are_pruned(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl", retention_days=1)
    stale = {
        "platform": "douyin",
        "url": "https://old",
        "input": "old",
        "firstSeen": "2020-01-01T00:00:00",
        "lastSeen": "2020-01-01T00:00:00",
        "count": 1,
        "kinds": {"gone": 1},
        "kind": "gone",
        "lastCode": "WORK_UNAVAILABLE",
        "lastMessage": "",
        "lastMs": 1,
        "lastIp": "",
        "sources": {"web": 1},
    }
    store._items[("douyin", "https://old")] = stale

    _fail(store, url="https://fresh")
    urls = {item["url"] for item in store.report()["items"]}
    assert urls == {"https://fresh"}


def test_sorting_puts_most_actionable_first(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, url="https://gone", code="WORK_UNAVAILABLE")
    _fail(store, url="https://bug", code="PARSE_FAILED")
    _fail(store, url="https://input", code="UNSUPPORTED_PLATFORM", platform="_rejected")

    kinds = [item["kind"] for item in store.report()["items"]]
    assert kinds == ["input", "parser_bug", "gone"]  # gone 排最后


def test_kind_filter(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, url="https://gone", code="WORK_UNAVAILABLE")
    _fail(store, url="https://bug", code="PARSE_FAILED")

    report = store.report(kinds={"parser_bug"})
    assert report["total"] == 1
    assert report["items"][0]["url"] == "https://bug"


# ---------------------------------------------------------------- 落盘
def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "f.jsonl"
    store = failures_module.Failures(path)
    _fail(store, url="https://a/1")
    _fail(store, url="https://a/1")
    store.flush()

    lines = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert len(lines) == 1  # 一行一个 JSON 对象，且已去重
    assert lines[0]["count"] == 2

    restored = failures_module.Failures(path).report()
    assert restored["total"] == 1
    assert restored["items"][0]["count"] == 2


def test_corrupt_line_is_skipped(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_text('{"platform":"a","url":"https://a"}\n这不是 JSON\n', "utf-8")
    store = failures_module.Failures(path)  # 不应抛异常
    assert store.size() == 1


# ---------------------------------------------------------------- 接口
def test_resolve_removes_fixed_entry(tmp_path):
    store = failures_module.Failures(tmp_path / "f.jsonl")
    _fail(store, url="https://a/1")

    assert store.size() == 1
    assert store.resolve("https://a/1") is True
    assert store.size() == 0
    assert store.resolve("https://a/never-seen") is False


def test_successful_parse_clears_the_entry(local, monkeypatch):
    """修好之后（同一链接能解析了）应从待办移除，否则复查时会被已修复的条目干扰。"""
    from app.parsers.kuaishou import ParsedWork

    monkeypatch.setattr(
        main_module, "extract_share_url", lambda text: ("douyin", "https://v.douyin.com/was-broken")
    )
    calls = {"n": 0}

    async def flaky(url, timeout=None, client=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise WORK_UNAVAILABLE
        return ParsedWork(type="video", title="t", author="a", video_url="https://cdn.example/v.mp4")

    monkeypatch.setattr(main_module, "parse_douyin", flaky)

    assert client.post("/api/parse", json={"input": "x"}).status_code == 422
    assert client.get("/api/stats/failures").json()["total"] == 1  # 失败：进待办

    assert client.post("/api/parse", json={"input": "x"}).status_code == 200
    assert client.get("/api/stats/failures").json()["total"] == 0  # 修好：出待办


def test_endpoint_hidden_from_proxied_requests(local):
    response = client.get("/api/stats/failures", headers={"x-real-ip": "1.2.3.4"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_endpoint_returns_json(local):
    failures_module.SAMPLES.record(
        url="https://v.douyin.com/x",
        input_text="看看这个 https://v.douyin.com/x",
        platform="douyin",
        code="PARSE_FAILED",
        message="解析失败",
        ms=10,
        source="web",
    )
    body = client.get("/api/stats/failures").json()
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["items"][0]["platform"] == "douyin"


def test_endpoint_text_is_ready_for_ai(local):
    failures_module.SAMPLES.record(
        url="https://v.douyin.com/x",
        input_text="口令 https://v.douyin.com/x",
        platform="douyin",
        code="PARSE_FAILED",
        message="解析失败",
        ms=10,
        source="web",
    )
    response = client.get("/api/stats/failures?fmt=text")
    assert response.headers["content-type"].startswith("text/plain")
    assert "解析失败样本" in response.text
    assert "input=输入未被识别" in response.text  # 自带字段含义，可直接丢给 AI
    assert "https://v.douyin.com/x" in response.text


def test_endpoint_supports_kind_filter(local):
    failures_module.SAMPLES.record(
        url="https://gone", input_text="x", platform="douyin",
        code="WORK_UNAVAILABLE", message="作品不可用", ms=1, source="web",
    )
    failures_module.SAMPLES.record(
        url="https://bug", input_text="y", platform="douyin",
        code="PARSE_FAILED", message="解析失败", ms=1, source="web",
    )
    body = client.get("/api/stats/failures?kind=input,parser_bug").json()
    assert body["total"] == 1
    assert body["items"][0]["url"] == "https://bug"


# ---------------------------------------------------------------- 端到端埋点
def test_platform_failure_is_journalled(local, monkeypatch):
    monkeypatch.setattr(
        main_module, "extract_share_url", lambda text: ("douyin", "https://v.douyin.com/e2e")
    )

    async def boom(url, timeout=None, client=None):
        raise WORK_UNAVAILABLE

    monkeypatch.setattr(main_module, "parse_douyin", boom)
    assert client.post("/api/parse", json={"input": "口令 https://v.douyin.com/e2e"}).status_code == 422

    items = client.get("/api/stats/failures").json()["items"]
    assert len(items) == 1
    assert items[0]["url"] == "https://v.douyin.com/e2e"
    assert items[0]["kind"] == "gone"
    assert items[0]["input"] == "口令 https://v.douyin.com/e2e"
    assert items[0]["lastCode"] == "WORK_UNAVAILABLE"


def test_unsupported_input_is_journalled(local):
    assert client.post("/api/parse", json=NOISE_RESPONSE).status_code == 400

    items = client.get("/api/stats/failures").json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "input"
    # 平台虽不支持，链接仍要摘出来 —— 它是复现与接入新平台的起点
    assert items[0]["url"] == "https://www.youtube.com/watch?v=1"


def test_text_without_link_keeps_empty_url(local):
    assert client.post("/api/parse", json={"input": "这是一段没有链接的口令"}).status_code == 400

    items = client.get("/api/stats/failures").json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "input"
    assert items[0]["url"] == ""
    assert items[0]["input"] == "这是一段没有链接的口令"


def test_empty_input_is_not_journalled(local):
    assert client.post("/api/parse", json={"input": "   "}).status_code == 400
    assert client.get("/api/stats/failures").json()["total"] == 0


def test_successful_parse_is_not_journalled(local, monkeypatch):
    from app.parsers.kuaishou import ParsedWork

    monkeypatch.setattr(
        main_module, "extract_share_url", lambda text: ("kuaishou", "https://v.kuaishou.com/ok")
    )

    async def fake_parse(url, timeout=None, client=None):
        return ParsedWork(type="video", title="t", author="a", video_url="https://cdn.example/v.mp4")

    monkeypatch.setattr(main_module, "parse_kuaishou", fake_parse)
    assert client.post("/api/parse", json={"input": "x"}).status_code == 200
    assert client.get("/api/stats/failures").json()["total"] == 0  # 只记失败
