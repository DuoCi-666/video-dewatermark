"""好看视频（haokan.baidu.com / haokan.hao123.com）解析器测试。

全部离线：结构取自 2026-09 真实接口快照（vid=8906507373021707907）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.errors import ParseError
from app.parsers import haokan
from app.parsers.extract import PLATFORMS, extract_share_url, is_haokan_url
from app.parsers.kuaishou import ParsedWork

VID = "8906507373021707907"
SHARE = f"https://haokan.hao123.com/v?vid={VID}"
SHARE_BAIDU = f"https://haokan.baidu.com/v?vid={VID}"
SHARE_SV = (
    "https://sv.baidu.com/videoui/page/videoland?"
    'context=%7B%22nid%22%3A%22sv_8906507373021707907%22%7D'
)
TITLE = "COS"
AUTHOR = "王者荣耀安妮"
COVER = "https://vd3.bdstatic.com/mda-rkn3w1kufuiarvp7/poster.jpg"
SD_URL = "https://vd3.bdstatic.com/mda-rkn3w1kufuiarvp7/540p/h264_cae/sd.mp4?auth_key=aaa"
SC_URL = "https://vd3.bdstatic.com/mda-rkn3w1kufuiarvp7/720p_frame30/h264_cae/sc.mp4?auth_key=bbb"


def payload(status: int = 0, clarity=None, playurl: str = "", title: str = TITLE) -> dict:
    if clarity is None:
        clarity = [
            {"key": "sd", "rank": 0, "title": "标清", "url": SD_URL},
            {"key": "sc", "rank": 2, "title": "超清", "url": SC_URL},
        ]
    return {
        "status": status,
        "msg": "成功",
        "data": {
            "apiData": {
                "header": {
                    "description": f"{title},本视频由{AUTHOR}原创提供,6636次播放,好看视频是由百度团队打造"
                },
                "curVideoMeta": {
                    "id": VID,
                    "title": title,
                    "poster": COVER,
                    "playurl": playurl,
                    "clarityUrl": clarity,
                },
            }
        },
    }


def _transport(body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def _parse(url: str, body: dict, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(body, status)) as client:
        return await haokan.parse(url, client=client)


def test_extract_vid():
    assert haokan.extract_vid(SHARE) == VID
    assert haokan.extract_vid(SHARE_BAIDU) == VID
    assert haokan.extract_vid(SHARE_SV) == VID
    assert haokan.extract_vid("https://haokan.baidu.com/v") is None


def test_extract_work_ok():
    work = haokan.extract_work(payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.author == AUTHOR
    assert work.cover_url == COVER
    assert work.video_url == SC_URL
    assert [item.label for item in work.variants] == ["超清", "标清"]


def test_extract_work_falls_back_to_playurl():
    work = haokan.extract_work(payload(clarity=[], playurl=SD_URL))
    assert work.video_url == SD_URL


def test_extract_work_bad_status():
    with pytest.raises(ParseError) as exc:
        haokan.extract_work(payload(status=1))
    assert exc.value.code == "PARSE_FAILED"


def test_extract_work_no_url():
    with pytest.raises(ParseError) as exc:
        haokan.extract_work(payload(clarity=[], playurl=""))
    assert exc.value.code == "WORK_UNAVAILABLE"


def test_platform_registered():
    platform = next((item for item in PLATFORMS if item.key == "haokan"), None)
    assert platform is not None
    assert platform.name == "好看视频"
    assert "haokan.baidu.com" in platform.hosts
    assert "haokan.hao123.com" in platform.hosts
    assert "video" in platform.kinds
    assert platform.signed_media is True
    assert is_haokan_url(SHARE) is True
    assert is_haokan_url("https://haokan.baidu.com.evil.com/v") is False
    assert is_haokan_url("https://www.baidu.com/s?wd=1") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {SHARE}")
    assert key == "haokan"
    assert url == SHARE


def test_parse_end_to_end():
    work = asyncio.run(_parse(SHARE, payload()))
    assert work.video_url == SC_URL
    assert work.title == TITLE
    assert work.author == AUTHOR


def test_parse_bad_url_raises():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse("https://haokan.baidu.com/v", payload()))
    assert exc.value.code == "PARSE_FAILED"
