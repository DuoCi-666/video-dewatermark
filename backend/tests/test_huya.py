"""虎牙（v.huya.com）解析器测试。

全部离线：结构取自 2026-09 真实接口快照（vid=1068614110）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.errors import ParseError
from app.parsers import huya
from app.parsers.extract import PLATFORMS, extract_share_url, is_huya_url
from app.parsers.kuaishou import ParsedWork

VID = "1068614110"
SHARE = f"https://v.huya.com/{VID}.html"
TITLE = "【赖神】极致细节！学会了你也是对线之王"
COVER = "https://v-huya-img2.msstatic.com/screenshot/x/1068614110/9.jpg"
AUTHOR = "LING-赖神"
VIDEO_URL = "http://videoal-platform.cdn.huya.com/leaf/1048585/x/9426827"


def payload(status: int = 200, definitions=None, video_url: str = "") -> dict:
    if definitions is None:
        definitions = [{"url": VIDEO_URL, "definition": "4000"}]
    return {
        "status": status,
        "data": {
            "moment": {
                "videoInfo": {
                    "videoTitle": TITLE,
                    "videoCover": COVER,
                    "nickName": AUTHOR,
                    "uid": "1",
                    "videoUrl": video_url,
                    "definitions": definitions,
                }
            }
        },
    }


def _transport(body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def _parse(url: str, body: dict, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(body, status)) as c:
        return await huya.parse(url, client=c)


# ---- 纯函数 ----


def test_extract_vid():
    assert huya.extract_vid(SHARE) == VID
    assert huya.extract_vid("https://v.huya.com/") is None


def test_extract_work_ok():
    work = huya.extract_work(payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.author == AUTHOR
    assert work.cover_url == COVER
    assert work.video_url == VIDEO_URL


def test_extract_work_falls_back_to_video_url():
    work = huya.extract_work(payload(definitions=[], video_url=VIDEO_URL))
    assert work.video_url == VIDEO_URL


def test_extract_work_bad_status():
    with pytest.raises(ParseError) as exc:
        huya.extract_work(payload(status=500))
    assert exc.value.code == "PARSE_FAILED"


def test_extract_work_no_url():
    with pytest.raises(ParseError) as exc:
        huya.extract_work(payload(definitions=[]))
    assert exc.value.code == "WORK_UNAVAILABLE"


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "huya"), None)
    assert platform is not None
    assert platform.name == "虎牙"
    assert "huya.com" in platform.hosts
    assert "video" in platform.kinds
    assert is_huya_url("https://v.huya.com/1.html") is True
    assert is_huya_url("https://huya.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {SHARE}")
    assert key == "huya"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(SHARE, payload()))
    assert work.video_url == VIDEO_URL
    assert work.title == TITLE


def test_parse_bad_url_raises():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse("https://v.huya.com/", payload()))
    assert exc.value.code == "PARSE_FAILED"
