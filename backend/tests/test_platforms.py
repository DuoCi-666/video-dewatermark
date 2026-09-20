"""平台清单（/api/platforms）的测试。

核心保障：注册表 ↔ 识别路由 ↔ 前端清单三者一致，
不会出现「后端已支持但界面没显示」的情况。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.parsers.extract import PLATFORMS, extract_share_url, platform_list

client = TestClient(app)


def test_endpoint_returns_registry():
    body = client.get("/api/platforms").json()
    assert body["ok"] is True
    platforms = body["platforms"]
    assert len(platforms) == len(PLATFORMS) >= 5
    for item in platforms:
        assert item["key"] and item["name"] and item["hosts"]


def test_endpoint_is_cacheable_and_public():
    response = client.get("/api/platforms")
    assert response.status_code == 200
    assert "max-age" in response.headers.get("cache-control", "")


def test_every_registered_platform_is_actually_routable():
    """注册表里的每个平台都必须能被 extract_share_url 识别到对应 key。"""
    for item in platform_list():
        for host in item["hosts"]:
            key, url = extract_share_url(f"看看这个 https://{host}/abc")
            assert key == item["key"], f"{host} 被识别成了 {key}"
            assert url == f"https://{host}/abc"


def test_unknown_host_still_rejected():
    from app.errors import ParseError

    try:
        extract_share_url("https://www.youtube.com/watch?v=1")
    except ParseError as exc:
        assert exc.code == "UNSUPPORTED_PLATFORM"
    else:  # pragma: no cover
        raise AssertionError("未支持的平台应该抛 UNSUPPORTED_PLATFORM")


def test_platform_names_are_unique():
    names = [item["name"] for item in platform_list()]
    keys = [item["key"] for item in platform_list()]
    assert len(set(names)) == len(names)
    assert len(set(keys)) == len(keys)
