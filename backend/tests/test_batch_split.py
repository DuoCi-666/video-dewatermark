"""批量解析「口令拆分」的端到端测试。

场景：分享口令常被 App 换行截断（典型：小红书复制出来是两行、只有一行带链接）。
修复前按行拆分 → 第二行没有链接也当成独立条目去解析，报「未识别到有效链接」，
体验上像「一条口令算成两条」。修复后按链接边界切段，纯文本行只作为上下文。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import batch
from app import main as main_module
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_batch_store():
    batch.STORE.reset()
    yield
    batch.STORE.reset()


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


def _submit_and_poll(items: list[str], timeout: float = 5.0) -> dict:
    response = client.post("/api/batch", json={"items": items})
    assert response.status_code == 200
    job_id = response.json()["jobId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/batch/{job_id}").json()["job"]
        if snap["status"] == "done":
            return snap
        time.sleep(0.02)
    return client.get(f"/api/batch/{job_id}").json()["job"]


def test_xhs_share_token_counts_as_one(fake_parse_ok):
    """用户报告的场景：两行口令只有一行带链接 → 只算 1 条。"""
    text = (
        "快来看 扫地僧 创作的故事《老板的英语补习》！1050+人点赞过这个作品，"
        " https://xhslink.cn/o/8R15HScfQYh\n"
        "存下口令，来【小红书】瞧瞧这篇~"
    )
    snap = _submit_and_poll([text])
    assert snap["total"] == 1
    assert len(snap["items"]) == 1
    assert snap["items"][0]["ok"] is True


def test_two_links_still_count_as_two(fake_parse_ok):
    """真有多条链接时仍各算一条，不受影响。"""
    snap = _submit_and_poll([
        "看这个 https://xhslink.cn/o/aaa\n第二个 https://xhslink.cn/o/bbb"
    ])
    assert snap["total"] == 2
    assert all(item["ok"] for item in snap["items"])


def test_same_link_in_different_wording_deduped(fake_parse_ok):
    """同一链接出现在两条不同文案里 → 按链接去重成 1 条。"""
    snap = _submit_and_poll([
        "https://xhslink.cn/o/aaa 说明",
        "再看 https://xhslink.cn/o/aaa 续行",
    ])
    assert snap["total"] == 1
