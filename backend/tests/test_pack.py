"""批量 ZIP 打包（批量第二阶段：一键下载）的测试。

覆盖：
- 媒体条目收集（只取主媒体：视频默认档 / 图片全原图；失败项跳过）
- 触发打包要求 job 已完成；未知 job / 未完成 返回结构化错误
- 拉取字节 → 生成 zip → 可下载，校验 zip 内文件名与字节内容
- 单条/整体失败时逐条跳过、整体记为 failed
"""
from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import batch, main as m
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_and_isolated_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("PACK_STORE_PATH", str(tmp_path / "packs"))
    m._PACKS.clear()
    m._PACK_ORDER.clear()
    yield
    m._PACKS.clear()
    m._PACK_ORDER.clear()


def _snapshot_done():
    return {
        "status": "done",
        "items": [
            {
                "ok": True,
                "index": 0,
                "result": {
                    "ok": True,
                    "type": "video",
                    "title": "clip one",
                    "variants": [{"mediaUrl": "/api/media/video_tok"}],
                },
            },
            {
                "ok": True,
                "index": 1,
                "result": {
                    "ok": True,
                    "type": "images",
                    "title": "gallery/with<slash>",
                    "images": [
                        {"previewUrl": "/api/media/img_1"},
                        {"previewUrl": "/api/media/img_2"},
                    ],
                },
            },
            {"ok": False, "index": 2, "code": "INVALID_LINK", "result": None},
        ],
    }


class FakeUpstream:
    def __init__(self, data: bytes):
        self.data = data

    async def aiter_bytes(self):
        yield self.data

    async def aclose(self):
        pass


class FakeClient:
    async def aclose(self):
        pass


def test_media_token_helper():
    assert m._media_token("/api/media/abc123") == "abc123"
    assert m._media_token("https://cdn/x.mp4") is None
    assert m._media_token("") is None


def test_collect_media_only_primary_and_ok_items():
    snap = _snapshot_done()
    entries = m._collect_media(snap)
    # 视频 1 条 + 图片 2 条 = 3；失败项被跳过
    assert len(entries) == 3
    tokens = [e[0] for e in entries]
    assert "video_tok" in tokens and "img_1" in tokens and "img_2" in tokens
    # 标题里的非法字符被清理成安全文件名
    for _, _, folder in entries:
        assert "/" not in folder and "\\" not in folder and "<" not in folder


def _poll_pack(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pack = client.get(f"/api/batch/{job_id}/zip").json()["pack"]
        if pack["status"] != "running":
            return pack
        time.sleep(0.02)
    return client.get(f"/api/batch/{job_id}/zip").json()["pack"]


def test_zip_preconditions(monkeypatch):
    monkeypatch.setattr(batch.STORE, "get", lambda jid: None)
    assert client.post("/api/batch/nope/zip").status_code == 404

    monkeypatch.setattr(batch.STORE, "get", lambda jid: {"status": "running", "items": []})
    r = client.post("/api/batch/job/zip")
    assert r.status_code == 409
    assert r.json()["code"] == "JOB_NOT_READY"


def test_zip_no_media(monkeypatch):
    monkeypatch.setattr(batch.STORE, "get", lambda jid: {"status": "done", "items": []})
    r = client.post("/api/batch/job/zip")
    assert r.status_code == 409
    assert r.json()["code"] == "NO_MEDIA"


def test_zip_packs_and_downloads(monkeypatch):
    monkeypatch.setattr(batch.STORE, "get", lambda jid: _snapshot_done())

    async def _fake_open_media(token, range_header=None):
        return FakeClient(), FakeUpstream(b"somedata")

    monkeypatch.setattr(m, "_open_media", _fake_open_media)

    job_id = "job_ok"
    r = client.post(f"/api/batch/{job_id}/zip")
    assert r.status_code == 200
    assert r.json()["pack"]["status"] == "running"
    assert r.json()["pack"]["total"] == 3

    pack = _poll_pack(job_id)
    assert pack["status"] == "done"
    assert pack["pulled"] == 3
    assert pack["canDownload"] is True

    dl = client.get(f"/api/batch/{job_id}/zip/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(dl.content))
    names = zf.namelist()
    assert any(name.endswith("video.mp4") for name in names)
    assert any(name.endswith("image_1.jpg") for name in names)
    assert all(zf.read(name) == b"somedata" for name in names)


def test_zip_download_before_ready(monkeypatch):
    monkeypatch.setattr(batch.STORE, "get", lambda jid: _snapshot_done())
    assert client.get("/api/batch/never/zip/download").status_code == 404