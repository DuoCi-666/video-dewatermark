"""腾讯视频（v.qq.com）解析器测试。

全部离线：``getinfo`` 的 JSONP 结构取自 2026-09 真实响应快照
（vid=c0329j2hqcf《你瞒我瞒》剧场版）。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.errors import ParseError
from app.parsers import qqvideo
from app.parsers.extract import PLATFORMS, extract_share_url, is_qqvideo_url
from app.parsers.kuaishou import ParsedWork

PAGE = "https://v.qq.com/x/page/c0329j2hqcf.html"
COVER_PAGE = "https://v.qq.com/x/cover/mzc00200803dr6b/c0329j2hqcf.html"
MOBILE = "https://m.v.qq.com/x/m/play?vid=c0329j2hqcf"
SERIES = "https://v.qq.com/x/cover/mzc00200803dr6b.html"

VID = "c0329j2hqcf"
TITLE = "《你瞒我瞒》剧场版"
FN = "szg_42133883_50001_90c269b788394c0cb26fb88592f8a898.f622.mp4"
BASE = "http://43.141.131.78/vhot2.qqvideo.tc.qq.com/abc/svp_50001/"
VKEY = "FB873B4F2BA4B58F"
VIDEO_URL = f"{BASE}{FN}?vkey={VKEY}"
COVER = f"https://puui.qpic.cn/vpic_cover/{VID}/{VID}_hz.jpg/496"


def payload(em: int = 0) -> dict:
    return {
        "em": em,
        "msg": "ok" if em == 0 else "不可观看",
        "vl": {
            "vi": [
                {
                    "vid": VID,
                    "ti": TITLE,
                    "fn": FN,
                    "fvkey": VKEY,
                    "ul": {"ui": [{"url": BASE}]},
                }
            ]
        },
    }


def jsonp(data: dict) -> str:
    return "QZOutputJson=" + json.dumps(data, ensure_ascii=False) + ";"


def _transport(text: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=text)

    return httpx.MockTransport(handler)


async def _parse(url: str, text: str, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(text, status)) as c:
        return await qqvideo.parse(url, client=c)


# ---- 纯函数 ----


def test_extract_vid():
    assert qqvideo.extract_vid(PAGE) == VID
    assert qqvideo.extract_vid(COVER_PAGE) == VID
    assert qqvideo.extract_vid(MOBILE) == VID
    assert qqvideo.extract_vid(SERIES) is None


def test_is_series_page():
    assert qqvideo.is_series_page(SERIES) is True
    assert qqvideo.is_series_page(COVER_PAGE) is False


def test_parse_jsonp():
    data = qqvideo.parse_jsonp(jsonp(payload()))
    assert isinstance(data, dict)
    assert data["em"] == 0
    assert qqvideo.parse_jsonp("QZOutputJson={bad json};") is None


def test_extract_work_ok():
    work = qqvideo.extract_work(payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.video_url == VIDEO_URL
    assert work.cover_url == COVER


def test_extract_work_error_em():
    with pytest.raises(ParseError) as exc:
        qqvideo.extract_work(payload(em=61))
    assert exc.value.code == "WORK_UNAVAILABLE"


def test_extract_work_missing_fields():
    data = payload()
    data["vl"]["vi"][0]["fvkey"] = ""
    with pytest.raises(ParseError) as exc:
        qqvideo.extract_work(data)
    assert exc.value.code == "PARSE_FAILED"


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "qqvideo"), None)
    assert platform is not None
    assert platform.name == "腾讯视频"
    assert "v.qq.com" in platform.hosts
    assert "video" in platform.kinds
    assert platform.signed_media is True
    assert is_qqvideo_url("https://v.qq.com/x/page/x.html") is True
    assert is_qqvideo_url("https://m.v.qq.com/x/m/play") is True
    assert is_qqvideo_url("https://v.qq.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {PAGE}")
    assert key == "qqvideo"
    assert url == PAGE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(PAGE, jsonp(payload())))
    assert work.video_url == VIDEO_URL
    assert work.title == TITLE


def test_parse_series_page_raises_profile_link():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse(SERIES, jsonp(payload())))
    assert exc.value.code == "PROFILE_LINK"
    assert "专辑" in exc.value.message
