from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ParseError
from app.main import app
from app.parsers import jimeng
from app.parsers.extract import extract_share_url
from app.parsers.kuaishou import ParsedWork

client = TestClient(app)

SHARE_SHORT = "https://jimeng.jianying.com/s/SdC6CP0EWRU/?t=8011"
SHARE_LONG = (
    "https://jimeng.jianying.com/activities/reflux/mproject?id=7677638530138508568&scene=cast_template"
)
ITEM_ID = "7677638530138508568"
VIDEO = "https://v3-dreamnia.jimeng.com/f4a9/video/tos/cn/origin.mp4?lr=display_watermark_ending"
COVER = "https://p26-sign.douyinpic.com/tos-cn-p-148450/cover~tplv-noop.image?x-expires=1"


def item_info_payload() -> dict:
    return {
        "ret": "0",
        "errmsg": "success",
        "logid": "2026",
        "systime": "1789530408",
        "data": {
            "title": "",
            "description": "替换角色，情节不变",
            "author": {"name": "黎明与黑暗", "uid": "12373844"},
            "common_attr": {"id": ITEM_ID, "title": "", "description": "替换角色，情节不变"},
            "video": {
                "cover_url": COVER,
                "duration": 11,
                "origin_video": {
                    "video_url": VIDEO,
                    "definition": "720p",
                    "width": 720,
                    "height": 1280,
                    "logo_type": "display_watermark_ending",
                },
            },
        },
    }


def test_id_from_long_share_link():
    assert jimeng._item_id_from(SHARE_LONG) == ITEM_ID
    assert jimeng._item_id_from("https://jimeng.jianying.com/s/AbC123/?t=8011") is None


def test_pid_from_location():
    location = f"https://jimeng.jianying.com/activities/reflux/mproject?ab_vid=x&id={ITEM_ID}&scene=cast_template"
    assert jimeng._pid_from_location(location) == ITEM_ID
    assert jimeng._pid_from_location("https://jimeng.jianying.com/x?id=abc") is None
    assert jimeng._pid_from_location("") is None


def test_extract_share_url_jimeng():
    key, url = extract_share_url(f"即梦作品 {SHARE_SHORT}")
    assert key == "jimeng"
    assert url == SHARE_SHORT
    assert extract_share_url(SHARE_LONG)[0] == "jimeng"


def test_work_extraction():
    work = jimeng.extract_work(item_info_payload()["data"])
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    # title 为空串时回落到 description
    assert work.title == "替换角色，情节不变"
    assert work.author == "黎明与黑暗"
    assert work.cover_url == COVER
    assert work.video_url == VIDEO
    assert work.video_fallbacks == []
    assert [(v.label, v.rank) for v in work.variants] == [("720p", 0)]


def test_title_fallback_chain():
    data = item_info_payload()["data"]
    # title 有值时优先
    data["title"] = "封面文案"
    assert jimeng.extract_work(data).title == "封面文案"
    # title / description 均为空 → 默认值
    data["title"] = ""
    data["description"] = ""
    data["common_attr"].pop("title", None)
    data["common_attr"].pop("description", None)
    assert jimeng.extract_work(data).title == jimeng.DEFAULT_TITLE


def test_author_default_when_missing():
    data = item_info_payload()["data"]
    data.pop("author")
    assert jimeng.extract_work(data).author == jimeng.DEFAULT_AUTHOR


def test_no_video_url_raises_work_unavailable():
    data = item_info_payload()["data"]
    data["video"]["origin_video"].pop("video_url")
    with pytest.raises(ParseError) as info:
        jimeng.extract_work(data)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_item_not_exist_maps_to_work_unavailable():
    with pytest.raises(ParseError) as info:
        asyncio.run(
            _parse_with_transport(
                _transport_json(200, {"ret": "2032", "errmsg": "itemId not exist", "data": None})
            )
        )
    assert info.value.code == "WORK_UNAVAILABLE"


def test_invalid_parameter_maps_to_parse_failed():
    with pytest.raises(ParseError) as info:
        asyncio.run(
            _parse_with_transport(
                _transport_json(200, {"ret": "1000", "errmsg": "invalid parameter", "data": None})
            )
        )
    assert info.value.code == "PARSE_FAILED"


def test_parse_short_link_end_to_end():
    work = asyncio.run(_parse_with_transport(_transport_json(200, item_info_payload())))
    assert work.type == "video"
    assert work.video_url == VIDEO
    assert work.author == "黎明与黑暗"


def test_jimeng_is_registered_as_signed_media():
    from app.parsers.extract import PLATFORMS, is_jimeng_url, signed_media_platforms

    platform = next((item for item in PLATFORMS if item.key == "jimeng"), None)
    assert platform is not None
    assert platform.name == "即梦AI"
    assert "jimeng.jianying.com" in platform.hosts
    assert is_jimeng_url("https://jimeng.jianying.com/s/abc/") is True
    assert is_jimeng_url("https://jimeng.jianying.com.evil.com/x") is False
    assert "jimeng" in signed_media_platforms()


def test_refresh_info_enabled_for_jimeng():
    from app.main import _refresh_info

    info = _refresh_info("jimeng", SHARE_SHORT, "video")
    assert info == {"platform": "jimeng", "share_url": SHARE_SHORT, "kind": "video", "index": 0}


def test_upstream_headers_adds_referer_for_jimeng_cdn():
    from app.main import _upstream_headers

    headers = _upstream_headers(VIDEO)
    assert headers["Referer"] == "https://jimeng.jianying.com/"
    # 封面在 douyinpic 域，不应带 Referer
    assert "Referer" not in _upstream_headers(COVER)


# ---- 辅助：用 MockTransport 驱动 parse（短链 302 → 接口返回）----


def _transport_json(status: int, payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                302,
                headers={"location": f"https://jimeng.jianying.com/activities/reflux/mproject?id={ITEM_ID}&ab_vid=x"},
            )
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


async def _parse_with_transport(transport: httpx.MockTransport):
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as c:
        return await jimeng.parse(SHARE_SHORT, client=c)
