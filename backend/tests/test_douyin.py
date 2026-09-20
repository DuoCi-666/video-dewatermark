import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import UNSUPPORTED_PLATFORM, ParseError
from app.main import app
from app.parsers.douyin import (
    _extract_router_json,
    _find_item,
    _no_watermark,
    _video_id_from,
)
from app.parsers.extract import extract_share_url

client = TestClient(app)

ROUTER_SAMPLE = (
    '<script>window._ROUTER_DATA = {"loaderData":{"video_(id)/page":'
    '{"videoInfoRes":{"status_code":0,"item_list":[{"desc":"测试标题",'
    '"author":{"nickname":"作者甲"},"video":{"play_addr":{"uri":"v0300abc",'
    '"url_list":["https://aweme.snssdk.com/aweme/v1/playwm/?ratio=720p&video_id=v0300abc"]}}}]}}}};'
    "</script>"
)


def test_extract_share_url_platforms():
    assert extract_share_url("看看 https://v.douyin.com/ABC123/ 好东西")[0] == "douyin"
    assert (
        extract_share_url("https://www.iesdouyin.com/share/video/123/")[0] == "douyin"
    )
    assert extract_share_url("https://v.kuaishou.com/xxxx 口令")[0] == "kuaishou"
    with pytest.raises(ParseError) as exc:
        extract_share_url("https://www.youtube.com/watch?v=1")
    assert exc.value.code == UNSUPPORTED_PLATFORM.code


def test_no_watermark_replacement():
    wm = "https://aweme.snssdk.com/aweme/v1/playwm/?ratio=720p&video_id=v1"
    assert _no_watermark(wm) == (
        "https://aweme.snssdk.com/aweme/v1/play/?ratio=1080p&video_id=v1"
    )


def test_router_json_and_item_finding():
    router = _extract_router_json(ROUTER_SAMPLE)
    assert router is not None
    item = _find_item(router)
    assert item is not None
    assert item["desc"] == "测试标题"
    assert _find_item({"loaderData": {"x": {"videoInfoRes": {"item_list": []}}}}) is None


def test_video_id_extraction():
    assert _video_id_from("https://www.iesdouyin.com/share/video/123456/?x=1") == "123456"
    assert _video_id_from("https://www.iesdouyin.com/share/note/654321/") == "654321"
    assert _video_id_from("https://www.douyin.com/user/abc") is None


def test_parse_api_routes_douyin(monkeypatch):
    import app.main as m
    from app.parsers.kuaishou import ParsedWork

    called = {"platform": ""}

    async def fake_douyin(url, timeout=15.0, client=None):
        called["platform"] = "douyin"
        return ParsedWork(
            type="video",
            title="抖音标题",
            author="抖音作者",
            video_url="https://aweme.snssdk.com/aweme/v1/play/?video_id=v9",
        )

    monkeypatch.setattr(m, "parse_douyin", fake_douyin)
    r = client.post(
        "/api/parse",
        json={"input": "https://v.douyin.com/PStsaN__umI/ 世间的王 @某人 #话题"},
    )
    body = r.json()
    assert called["platform"] == "douyin"
    assert body["ok"] is True
    assert body["type"] == "video"
    assert body["title"] == "抖音标题"
    assert body["variants"][0]["label"] == "默认"
