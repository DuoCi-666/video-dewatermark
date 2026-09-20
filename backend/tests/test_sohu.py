"""搜狐视频（tv.sohu.com）解析器测试。

全部离线：API 结构取自 2026-09 真实响应快照（vid=744119670）。
"""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from app.errors import ParseError
from app.parsers import sohu
from app.parsers.extract import PLATFORMS, extract_share_url, is_sohu_url
from app.parsers.kuaishou import ParsedWork

VID = "744119670"
SHARE = f"https://tv.sohu.com/us/266683275/{VID}.shtml"
TITLE = "搜狐垂钓跨界达人赛秋关特别版直播回放"
VIDEO_URL = "http://data.vod.itc.cn/?k=abc"
COVER = "http://e3f49eaa46b57.cdn.sohucs.com/300106/x.jpg"
AUTHOR = "搜狐垂钓"


def payload(status: int = 200, url: str = VIDEO_URL) -> dict:
    return {
        "status": status,
        "data": {
            "video_name": TITLE,
            "url_high_mp4": url,
            "originalCutCover": COVER,
            "user": {"nickname": AUTHOR, "user_id": "1"},
        },
    }


def _transport(body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def _parse(url: str, body: dict, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(body, status)) as c:
        return await sohu.parse(url, client=c)


# ---- 纯函数 ----


def test_extract_vid():
    assert sohu.extract_vid(SHARE) == VID
    assert sohu.extract_vid(f"https://my.tv.sohu.com/us/266683275/{VID}.shtml") == VID
    b64 = base64.b64encode(f"us/266683275/{VID}.shtml".encode()).decode()
    assert sohu.extract_vid(f"https://tv.sohu.com/v/{b64}.html") == VID
    assert sohu.extract_vid("https://tv.sohu.com/") is None


def test_extract_work_ok():
    work = sohu.extract_work(payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.author == AUTHOR
    assert work.cover_url == COVER
    assert work.video_url == VIDEO_URL


def test_extract_work_download_url_fallback():
    data = payload()
    data["data"]["url_high_mp4"] = ""
    data["data"]["download_url"] = VIDEO_URL
    assert sohu.extract_work(data).video_url == VIDEO_URL


def test_extract_work_multi_candidate_split():
    # url_high_mp4 实测是逗号分隔的多 CDN 候选串：必须拆成主地址 + 备用列表，
    # 整串塞进 video_url 会被媒体代理当单个 URL 回源而 404
    url = ",".join(f"http://cdn{i}.vod.itc.cn/?k=k{i}" for i in range(3))
    work = sohu.extract_work(payload(url=url))
    assert work.video_url == "http://cdn0.vod.itc.cn/?k=k0"
    assert work.video_fallbacks == [
        "http://cdn1.vod.itc.cn/?k=k1",
        "http://cdn2.vod.itc.cn/?k=k2",
    ]


def test_extract_work_candidates_filtered():
    # 空段与非 http 段被过滤，剩余合法候选正常产出
    work = sohu.extract_work(payload(url="http://cdn0.vod.itc.cn/?k=k0,,ftp://bad"))
    assert work.video_url == "http://cdn0.vod.itc.cn/?k=k0"
    assert work.video_fallbacks == []


def test_extract_work_bad_status():
    with pytest.raises(ParseError) as exc:
        sohu.extract_work(payload(status=404))
    assert exc.value.code == "PARSE_FAILED"


def test_extract_work_no_url():
    with pytest.raises(ParseError) as exc:
        sohu.extract_work(payload(url=""))
    assert exc.value.code == "WORK_UNAVAILABLE"


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "sohu"), None)
    assert platform is not None
    assert platform.name == "搜狐视频"
    assert "sohu.com" in platform.hosts
    assert "video" in platform.kinds
    assert is_sohu_url("https://tv.sohu.com/us/1/2.shtml") is True
    assert is_sohu_url("https://sohu.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {SHARE}")
    assert key == "sohu"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(SHARE, payload()))
    assert work.video_url == VIDEO_URL
    assert work.title == TITLE


def test_parse_bad_url_raises():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse("https://tv.sohu.com/", payload()))
    assert exc.value.code == "PARSE_FAILED"
