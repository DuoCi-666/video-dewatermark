"""批量解析（/api/batch 异步 job）的测试。

核心保障：
- 提交立即返回 job_id，内部逐条复用单条解析路径（后台 asyncio 调度）
- 逐条错误隔离：单条失败只标记该项，不中断其他条目
- 有界存储：超过 BATCH_JOBS_MAX 淘汰最老已完成 job，绝不淘汰运行中的
- 独立配额：批量接口用独立的批量令牌桶，不挤占单条的全局限流/并发
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import batch, guard
from app import main as main_module
from app.errors import WORK_UNAVAILABLE
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_batch_store(monkeypatch):
    batch.STORE.reset()
    yield
    batch.STORE.reset()


def _poll(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/batch/{job_id}").json()["job"]
        if snap["status"] == "done":
            return snap
        time.sleep(0.02)
    return client.get(f"/api/batch/{job_id}").json()["job"]


@pytest.fixture
def fake_parse_ok(monkeypatch):
    async def fake_parse(text, source, client_ip):
        return {
            "ok": True,
            "type": "video",
            "title": f"t:{text}",
            "author": "a",
            "coverUrl": None,
            "videoUrl": None,
            "downloadUrl": None,
            "images": [],
        }

    monkeypatch.setattr(main_module, "_parse_one", fake_parse)


def _fake_parse_flaky(monkeypatch, fail_texts: set[str]):
    async def fake_parse(text, source, client_ip):
        if text in fail_texts:
            raise WORK_UNAVAILABLE
        return {
            "ok": True,
            "type": "video",
            "title": f"t:{text}",
            "author": "a",
            "coverUrl": None,
            "videoUrl": None,
            "downloadUrl": None,
            "images": [],
        }

    monkeypatch.setattr(main_module, "_parse_one", fake_parse)


def test_batch_returns_job_id_immediately(fake_parse_ok):
    response = client.post("/api/batch", json={"items": ["https://v.kuaishou.com/a"]})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["jobId"]


def test_batch_happy_path_all_succeed(fake_parse_ok):
    links = [f"https://v.kuaishou.com/{i}" for i in range(5)]
    job_id = client.post("/api/batch", json={"items": links}).json()["jobId"]
    snap = _poll(job_id)
    assert snap["status"] == "done"
    assert snap["done"] == 5
    assert snap["okCount"] == 5
    assert snap["failCount"] == 0
    results = [item["result"]["title"] for item in snap["items"]]
    assert results == [f"t:{link}" for link in links]


def test_batch_error_isolation(monkeypatch):
    """单条失败只标记该项，其余正常完成。"""
    _fake_parse_flaky(monkeypatch, {"gone"})
    links = ["ok1", "gone", "ok3"]
    job_id = client.post("/api/batch", json={"items": links}).json()["jobId"]
    snap = _poll(job_id)
    assert snap["status"] == "done"
    assert snap["okCount"] == 2
    assert snap["failCount"] == 1
    by_input = {item["result"]["title"] for item in snap["items"] if item["ok"]}
    assert by_input == {"t:ok1", "t:ok3"}
    failed = next(item for item in snap["items"] if not item["ok"])
    assert failed["code"] == WORK_UNAVAILABLE.code
    assert failed["result"] is None


def test_batch_empty_returns_400():
    response = client.post("/api/batch", json={"items": ["   ", "\n"]})
    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_INPUT"


def test_batch_blank_and_duplicate_filtered(fake_parse_ok):
    """空行 / 纯空白被丢弃，重复链接只保留一条。"""
    response = client.post(
        "/api/batch",
        json={"items": ["https://v.kuaishou.com/a", "", "https://v.kuaishou.com/a\nhttps://v.kuaishou.com/b", "  "]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2  # a 去重，b 保留


def test_batch_too_many_items_rejected(fake_parse_ok):
    response = client.post("/api/batch", json={"items": [f"x{i}" for i in range(batch.MAX_ITEMS + 1)]})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_INPUT"


def test_batch_uses_own_rate_quota(monkeypatch):
    """批量接口只走批量令牌桶：批量限流不应消耗单条解析配额。"""
    before = guard.GUARD.bucket.size()
    guard.GUARD.configure(enabled=True, batch_rate_per_min=10 ** 6, batch_burst=10 ** 6)
    try:
        client.post("/api/batch", json={"items": ["https://v.kuaishou.com/a"]})
        client.post("/api/batch", json={"items": ["https://v.kuaishou.com/b"]})
        # 批量用了自己的 bucket，单条解析的 bucket 不应被消耗
        assert guard.GUARD.bucket.size() == before
    finally:
        guard.GUARD.configure(enabled=False, batch_rate_per_min=20, batch_burst=5)
        guard.GUARD.reset()


def test_store_is_bounded_and_keeps_running_jobs(monkeypatch):
    """超上限时优先淘汰「已完成」的 job，绝不淘汰运行中的。"""
    store = batch.JobStore(max_jobs=2)
    a = store.create(["linkA"], source="test", client_ip="0")  # running
    b = store.create(["linkB"], source="test", client_ip="0")
    b.status = "done"
    c = store.create(["linkC"], source="test", client_ip="0")  # running
    # 超出上限(2)，淘汰已完成 的 b，保留运行中的 a、c
    assert store.size() == 2
    assert store.get(a.id) is not None
    assert store.get(c.id) is not None
    assert store.get(b.id) is None


def test_batch_unknown_job_returns_404():
    response = client.get("/api/batch/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"