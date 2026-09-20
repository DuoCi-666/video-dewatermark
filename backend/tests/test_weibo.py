from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ParseError
from app.main import app
from app.parsers import weibo
from app.parsers.extract import extract_share_url
from app.parsers.kuaishou import ParsedWork

client = TestClient(app)

VIDEO_720 = "https://f.video.weibocdn.com/v0/720.mp4?label=mp4_720p&Expires=1&ssig=a"
VIDEO_HD = "https://f.video.weibocdn.com/v0/hd.mp4?label=mp4_hd&Expires=1&ssig=b"
VIDEO_LD = "https://f.video.weibocdn.com/v0/ld.mp4?label=mp4_ld&Expires=1&ssig=c"
COVER = "https://wx2.sinaimg.cn/orj480/71d1d305gy1cover.jpg"
IMG1 = "https://wx3.sinaimg.cn/mw2000/e2a8fd93gy1img1.jpg"
IMG2 = "https://wx2.sinaimg.cn/mw2000/e2a8fd93gy1img2.jpg"


def video_payload() -> dict:
    return {
        "ok": 1,
        "data": {
            "text": "手办太大了家里没地儿放 <br /> #微博视频号迎新计划#",
            "user": {"screen_name": "走路摇ZLY"},
            "page_info": {
                "type": "video",
                "page_pic": {"url": COVER},
                "urls": {
                    "mp4_720p_mp4": VIDEO_720,
                    "mp4_hd_mp4": VIDEO_HD,
                    "mp4_ld_mp4": VIDEO_LD,
                },
                "media_info": {"stream_url": VIDEO_LD, "stream_url_hd": VIDEO_HD, "duration": 10.1},
            },
        },
    }


def images_payload() -> dict:
    return {
        "ok": 1,
        "data": {
            "text": "朵莉亚〔幻珠鲛人〕🫧 摄影@照桥染柒",
            "user": {"screen_name": "-婉Yue-目标减到99斤"},
            "pics": [
                {"large": {"url": IMG1}},
                {"large": {"url": IMG2}},
            ],
        },
    }


def test_bid_to_mid_uses_known_vectors():
    # 官方已知对应关系：z0JH2lOMb → 3501756485200075
    assert weibo.bid_to_mid("z0JH2lOMb") == "3501756485200075"
    assert weibo.bid_to_mid("yAt1n2xRa") == "3486913690606804"


def test_mid_from_url_formats():
    assert weibo._mid_from_url("https://weibo.com/1909576453/RhW7J5zAN") == "5342750071328461"
    assert weibo._mid_from_url("https://m.weibo.cn/status/5342750071328461") == "5342750071328461"
    assert weibo._mid_from_url("https://weibo.com/") is None


def test_extract_share_url_weibo():
    key, url = extract_share_url("微博分享 https://weibo.com/1909576453/RhW7J5zAN")
    assert key == "weibo"
    assert url.startswith("https://weibo.com/")
    assert extract_share_url("https://m.weibo.cn/status/5342750071328461")[0] == "weibo"
    assert extract_share_url("https://weibo.cn/12345/abc")[0] == "weibo"


def test_video_work_extraction():
    work = weibo.extract_work(video_payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.author == "走路摇ZLY"
    assert work.title == "手办太大了家里没地儿放 #微博视频号迎新计划#"
    assert work.cover_url == COVER
    labels = [item.label for item in work.variants]
    assert labels == ["720p", "高清", "标清"]
    assert work.video_url == VIDEO_720
    assert work.video_fallbacks == [VIDEO_HD, VIDEO_LD]


def test_images_work_extraction():
    work = weibo.extract_work(images_payload())
    assert work.type == "images"
    assert work.author == "-婉Yue-目标减到99斤"
    assert work.image_urls == [IMG1, IMG2]
    assert work.image_groups == [[IMG1], [IMG2]]
    assert work.cover_url == IMG1


def test_deleted_status_maps_to_work_unavailable():
    with pytest.raises(ParseError) as info:
        weibo.extract_work({"ok": 0, "errno": 20101, "message": "该微博不存在"})
    assert info.value.code == "WORK_UNAVAILABLE"


def test_empty_media_raises_parse_failed():
    payload = images_payload()
    payload["data"]["pics"] = []
    with pytest.raises(ParseError) as info:
        weibo.extract_work(payload)
    assert info.value.code == "PARSE_FAILED"


def test_weibo_is_registered_as_signed_media():
    from app.parsers.extract import PLATFORMS, is_weibo_url, signed_media_platforms

    platform = next((item for item in PLATFORMS if item.key == "weibo"), None)
    assert platform is not None
    assert platform.name == "微博"
    assert "weibo.com" in platform.hosts
    assert is_weibo_url("https://weibo.com/1909576453/RhW7J5zAN") is True
    assert is_weibo_url("https://weibo.com.evil.com/x") is False
    assert "weibo" in signed_media_platforms()


def test_refresh_info_enabled_for_weibo():
    from app.main import _refresh_info

    info = _refresh_info("weibo", "https://weibo.com/1909576453/RhW7J5zAN", "video")
    assert info == {
        "platform": "weibo",
        "share_url": "https://weibo.com/1909576453/RhW7J5zAN",
        "kind": "video",
        "index": 0,
    }


def test_parse_api_routes_weibo(monkeypatch):
    import app.main as m

    called = {"url": ""}

    async def fake_parse(url, timeout=15.0, client=None):
        called["url"] = url
        return ParsedWork(
            type="video",
            title="微博视频",
            author="走路摇ZLY",
            video_url=VIDEO_720,
        )

    monkeypatch.setattr(m, "parse_weibo", fake_parse)
    response = client.post(
        "/api/parse",
        json={"input": "https://weibo.com/1909576453/RhW7J5zAN"},
    )
    body = response.json()
    assert called["url"].endswith("RhW7J5zAN")
    assert body["ok"] is True
    assert body["type"] == "video"
    assert body["title"] == "微博视频"
    assert body["variants"][0]["label"] == "默认"


class FakeResponse:
    def __init__(self, payload, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class SequenceClient:
    """按调用顺序依次返回响应：genvisitor、incarnate、statuses/show。"""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.cookies = httpx.Cookies()
        self.calls = []

    async def post(self, url, data=None, headers=None):
        self.calls.append(("post", url, data or {}))
        # 模拟 genvisitor 返回 tid
        if self._responses:
            self._responses.pop(0)
        return FakeResponse({"ok": 1}, text='window.gen_callback && gen_callback({"retcode":20000000,"data":{"tid":"01ABC"}});')

    async def get(self, url, params=None, headers=None):
        self.calls.append(("get", url, params or {}))
        return self._responses.pop(0) if self._responses else FakeResponse({"ok": 0, "errno": 20101, "message": "该微博不存在"})

    async def aclose(self):
        return None


def test_parse_happy_path_uses_show_api():
    # 传入共享 client：按序返回 genvisitor、incarnate、statuses/show 三个响应
    client_obj = SequenceClient(
        FakeResponse({"ok": 1}, text="gen"),
        FakeResponse({"ok": 1}, text="incarnate"),
        FakeResponse(video_payload()),
    )
    work = asyncio.run(weibo.parse("https://weibo.com/1909576453/RhW7J5zAN", client=client_obj))
    assert work.type == "video"
    assert work.video_url == VIDEO_720
    show_calls = [c for c in client_obj.calls if c[0] == "get" and "statuses/show" in c[1]]
    assert len(show_calls) == 1
    assert show_calls[0][2] == {"id": "5342750071328461"}


def test_parse_shows_deleted_as_work_unavailable():
    client_obj = SequenceClient(
        FakeResponse({"ok": 1}, text="gen"),
        FakeResponse({"ok": 1}, text="incarnate"),
        FakeResponse({"ok": 0, "errno": 20101, "message": "该微博不存在"}),
    )
    with pytest.raises(ParseError) as info:
        asyncio.run(weibo.parse("https://weibo.com/1909576453/RhW7J5zAN", client=client_obj))
    assert info.value.code == "WORK_UNAVAILABLE"


def test_parse_without_mid_fails():
    with pytest.raises(ParseError) as info:
        asyncio.run(weibo.parse("https://weibo.com/"))
    assert info.value.code == "PARSE_FAILED"