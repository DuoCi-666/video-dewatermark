import httpx
from fastapi.testclient import TestClient

from app import tokens
from app.main import app

client = TestClient(app)


def test_issue_sweeps_expired_tokens():
    old = tokens.issue("https://cdn.example/old.mp4", "video", "old.mp4")
    tokens._STORE[old].expires_at = 0
    fresh = tokens.issue("https://cdn.example/new.mp4", "video", "new.mp4")
    assert tokens.get(old) is None
    assert tokens.get(fresh) is not None


def test_token_reusable_within_ttl():
    token = tokens.issue("https://cdn.example/a.mp4", "video", "a.mp4")
    assert tokens.get(token) is not None
    assert tokens.get(token) is not None


def _fake_proxy_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_media_range_returns_206(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("range"):
            return httpx.Response(
                206,
                content=b"0123456789",
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": "bytes 0-9/64",
                    "Accept-Ranges": "bytes",
                },
            )
        return httpx.Response(
            200,
            content=b"0" * 64,
            headers={
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
                "Content-Length": "64",
            },
        )

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue("https://cdn.example/v.mp4", "video", "v.mp4")

    ranged = client.get(f"/api/media/{token}", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == "bytes 0-9/64"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.content == b"0123456789"

    full = client.get(f"/api/media/{token}")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert len(full.content) == 64


def test_media_range_forwarded_upstream(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["range"] = request.headers.get("range", "")
        return httpx.Response(206, content=b"xy", headers={"Content-Range": "bytes 0-1/64"})

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue("https://cdn.example/v.mp4", "video", "v.mp4")

    client.get(f"/api/media/{token}", headers={"Range": "bytes=100-199"})
    assert seen["range"] == "bytes=100-199"


def test_media_skips_candidate_with_empty_body(monkeypatch):
    """上游返回 200 但 Content-Length: 0（图床偶发抽风）时应换下一个候选，而不是渲染空白图。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "empty" in str(request.url):
            return httpx.Response(
                200, content=b"", headers={"Content-Type": "image/jpeg", "Content-Length": "0"}
            )
        return httpx.Response(
            200, content=b"ok", headers={"Content-Type": "image/jpeg", "Content-Length": "2"}
        )

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(
        ["https://cdn.example/empty.jpg", "https://cdn.example/good.jpg"], "image", "a.jpg"
    )

    response = client.get(f"/api/media/{token}")
    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(seen) == 2  # 空响应的候选被跳过


def test_media_reports_failure_when_all_candidates_are_empty(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"Content-Length": "0"})

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"], "image", "a.jpg")

    response = client.get(f"/api/media/{token}")
    assert response.status_code == 404


def test_token_survives_process_restart():
    token = tokens.issue("https://cdn.example/keep.mp4", "video", "keep.mp4")
    tokens.reset()
    restored = tokens.get(token)
    assert restored is not None
    assert restored.source_url == "https://cdn.example/keep.mp4"
    assert restored.filename == "keep.mp4"


def test_token_access_extends_ttl():
    token = tokens.issue("https://cdn.example/slide.mp4", "video", "slide.mp4")
    soon = tokens._now_ms() + tokens.TTL_MS // 4
    tokens._STORE[token].expires_at = soon
    tokens._persist_unlocked()
    got = tokens.get(token)
    assert got is not None
    assert got.expires_at > soon
    assert got.expires_at >= tokens._now_ms() + tokens.TTL_MS // 2


def test_expired_token_removed_from_disk():
    token = tokens.issue("https://cdn.example/gone.mp4", "video", "gone.mp4")
    tokens._STORE[token].expires_at = 0
    assert tokens.get(token) is None
    tokens.reset()
    assert tokens.get(token) is None
