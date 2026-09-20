import httpx
import pytest
from fastapi.testclient import TestClient

from app import tokens
from app.errors import ParseError, RISK_CONTROL
from app.main import app
from app.parsers.kuaishou import ParsedWork, _build_atlas_image_groups, _parse_html

client = TestClient(app)


def test_atlas_image_groups_multi_cdn():
    atlas = {
        "list": ["/pic/a.jpg", "/pic/b.jpg"],
        "cdn": ["cdn1.example", {"cdn": "cdn2.example"}],
    }
    groups = _build_atlas_image_groups(atlas)
    assert len(groups) == 2
    assert groups[0][0] == "https://cdn1.example/pic/a.jpg"
    assert "https://cdn2.example/pic/a.jpg" in groups[0]
    assert groups[1][1] == "https://cdn2.example/pic/b.jpg"


def test_caption_whitespace_collapsed():
    html = (
        '{"photoType":"VIDEO","caption":"标题一\\n标题二  空格",'
        '"userName":"作者","mainMvUrls":[{"url":"https://cdn.example/a.mp4"}]}'
    )
    work = _parse_html(html)
    assert work.type == "video"
    assert work.title == "标题一 标题二 空格"
    assert work.video_url == "https://cdn.example/a.mp4"


def test_extract_mp4_urls_fixed_whitespace_class():
    from app.parsers.kuaishou import _extract_mp4_urls

    html = 'x "https://a-b.kwimgs.com/path/mp4/v.mp4?sign=1&token=abc" y'
    urls = _extract_mp4_urls(html)
    assert urls == ["https://a-b.kwimgs.com/path/mp4/v.mp4?sign=1&token=abc"]


def test_risk_control_detected():
    with pytest.raises(ParseError) as exc:
        _parse_html("<html>please complete captcha to continue</html>")
    assert exc.value.code == RISK_CONTROL.code


def test_parse_cache_dedupes_same_link(monkeypatch):
    import app.main as m

    calls = {"n": 0}

    async def fake_parse(url, timeout=15.0, client=None):
        calls["n"] += 1
        return ParsedWork(
            type="video", title="t", author="a", video_url="https://cdn.example/x.mp4"
        )

    monkeypatch.setattr(m, "parse_kuaishou", fake_parse)
    r1 = client.post("/api/parse", json={"input": "https://v.kuaishou.com/cacheTest1"})
    r2 = client.post(
        "/api/parse", json={"input": "https://v.kuaishou.com/cacheTest1 附带口令文案"}
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 1
    assert r1.json()["videoUrl"] == r2.json()["videoUrl"]


def _fake_proxy_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_proxy_fallback_on_first_cdn_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "bad" in str(request.url):
            return httpx.Response(403, content=b"denied")
        return httpx.Response(200, content=b"GOODDATA", headers={"Content-Type": "video/mp4"})

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(
        ["https://cdn.example/bad.mp4", "https://cdn.example/good.mp4"], "video", "v.mp4"
    )
    r = client.get(f"/api/media/{token}")
    assert r.status_code == 200
    assert r.content == b"GOODDATA"


def test_proxy_all_fail_returns_404(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"denied")

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(
        ["https://cdn.example/bad1.mp4", "https://cdn.example/bad2.mp4"], "video", "v.mp4"
    )
    r = client.get(f"/api/media/{token}")
    assert r.status_code == 404


def test_proxy_retries_transient_404(monkeypatch):
    """单个候选偶发 404（CDN 抽风，如米游社图床）不应直接判失败：原地重试一次。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(404, content=b"missing")
        return httpx.Response(200, content=b"LATE", headers={"Content-Type": "image/jpeg"})

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(["https://cdn.example/flaky.jpg"], "image", "i.jpg")
    r = client.get(f"/api/media/{token}")
    assert r.status_code == 200
    assert r.content == b"LATE"
    assert calls["n"] == 2


def test_proxy_does_not_retry_permanent_403(monkeypatch):
    """签名失效类的 403 重试无意义：同一候选只请求一次，直接换下一个。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, content=b"expired")

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(["https://cdn.example/dead.jpg"], "image", "i.jpg")
    r = client.get(f"/api/media/{token}")
    assert r.status_code == 404
    assert calls["n"] == 1


def test_issue_multi_preserves_order_and_dedupes():
    urls = ["https://a/1", "https://b/2", "https://a/1"]
    token = tokens.issue_multi(urls, "video", "x.mp4")
    item = tokens.get(token)
    assert item.source_urls == ["https://a/1", "https://b/2"]
    assert item.source_url == "https://a/1"
