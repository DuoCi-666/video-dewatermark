"""央视网（cctv.com）解析器测试。

全部离线：用 httpx.MockTransport 返回视频页 HTML（含 ``var guid``）与央视网
视频信息 API 的 JSON，结构取自 2026-09 真实分享链接的抓取快照
（《新闻联播》 20260916 21:00，guid=2411c9fc8c724d9ba224f97f825adddd）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.errors import ParseError
from app.parsers import cctv
from app.parsers.extract import PLATFORMS, extract_share_url, is_cctv_url
from app.parsers.kuaishou import ParsedWork

SHARE = "https://tv.cctv.com/2026/09/16/VIDEcNtpUQG2MPSwUP1kpWFj260916.shtml"
GUID = "2411c9fc8c724d9ba224f97f825adddd"
MP4 = (
    "https://vod.cntv.lxdns.com/flash/mp4video63/TMS/2026/09/16/"
    "2411c9fc8c724d9ba224f97f825adddd_h264418000nero_aac32-1.mp4"
)
HLS = (
    "https://hls.cntv.lxdns.com/asp/hls/main/0303000a/3/default/"
    "2411c9fc8c724d9ba224f97f825adddd/main.m3u8?maxbr=2048"
)
TITLE = "《新闻联播》 20260916 21:00"
COVER = (
    "https://p5.img.cctvpic.com/photoAlbum/vms/standard/img/2026/9/16/"
    "VIDENq2nibhnlQyYR5AU0QBT260916.jpg"
)
CHANNEL = "CCTV-13高清"


def page_html(guid: str | None = GUID) -> str:
    line = f'var guid = "{guid}";' if guid else ""
    return f"<html><head><script>{line}</script></head><body>央视网</body></html>"


def api_payload(chapters=None, hls: str | None = HLS, status: str = "001") -> dict:
    if chapters is None:
        chapters = [{"duration": "1799.32", "image": COVER, "url": MP4}]
    return {
        "status": status,
        "title": TITLE,
        "image": COVER,
        "play_channel": CHANNEL,
        "hls_url": hls,
        "video": {
            "totalLength": "1799.32",
            "chapters": chapters,
            "validChapterNum": len(chapters),
        },
    }


def _transport(
    html: str, payload: dict | None = None, page_status: int = 200, api_status: int = 200
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "getHttpVideoInfo" in str(request.url):
            return httpx.Response(api_status, json=payload if payload is not None else {})
        return httpx.Response(page_status, text=html)

    return httpx.MockTransport(handler)


async def _parse(html: str, payload: dict | None = None, **kw):
    async with httpx.AsyncClient(transport=_transport(html, payload, **kw)) as c:
        return await cctv.parse(SHARE, client=c)


# ---- 纯函数 ----


def test_extract_guid():
    assert cctv.extract_guid(page_html()) == GUID
    assert cctv.extract_guid(page_html(None)) is None


def test_extract_work_video_mp4():
    work = cctv.extract_work(api_payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.author == CHANNEL
    assert work.cover_url == COVER
    assert work.video_url == MP4


def test_extract_work_hls_fallback():
    """分片 MP4 直链为空时回退 hls_url。"""
    work = cctv.extract_work(api_payload(chapters=[{"url": ""}]))
    assert work.video_url == HLS


def test_extract_work_bad_status():
    with pytest.raises(ParseError) as info:
        cctv.extract_work(api_payload(status="002"))
    assert info.value.code == "PARSE_FAILED"


def test_extract_work_no_media():
    with pytest.raises(ParseError) as info:
        cctv.extract_work(api_payload(chapters=[], hls=None))
    assert info.value.code == "WORK_UNAVAILABLE"


def test_title_and_author_defaults():
    payload = api_payload()
    payload["title"] = ""
    payload["play_channel"] = ""
    work = cctv.extract_work(payload)
    assert work.title == cctv.DEFAULT_TITLE
    assert work.author == cctv.DEFAULT_AUTHOR


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "cctv"), None)
    assert platform is not None
    assert platform.name == "央视网"
    assert "cctv.com" in platform.hosts
    assert "video" in platform.kinds
    assert is_cctv_url("https://tv.cctv.com/2026/09/16/x.shtml") is True
    assert is_cctv_url("https://news.cctv.com/x") is True
    assert is_cctv_url("https://cctv.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"分享 {SHARE}")
    assert key == "cctv"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(page_html(), api_payload()))
    assert work.type == "video"
    assert work.video_url == MP4
    assert work.title == TITLE


def test_parse_page_without_guid_raises():
    with pytest.raises(ParseError) as info:
        asyncio.run(_parse(page_html(None), api_payload()))
    assert info.value.code == "PARSE_FAILED"
