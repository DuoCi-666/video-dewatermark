"""AcFun（A站，acfun.cn）解析器测试。

全部离线：作品数据取自 2026-09 真实视频页（ac48851115）的结构快照：
``window.pageInfo = window.videoInfo = {...}``，播放地址在
``currentVideoInfo.ksPlayJson``（JSON 字符串）里。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.errors import ParseError
from app.parsers import acfun
from app.parsers.extract import PLATFORMS, extract_share_url, is_acfun_url
from app.parsers.kuaishou import ParsedWork

SHARE = "https://www.acfun.cn/v/ac48851115"
TITLE = "网络上常见的热门短视频集锦 第三千四百三十二期"
COVER = "https://tx-free-imgs.acfun.cn/newUpload/2176158_2256579895bb4722a50def5b9a81f749"
M3U8_A = "https://tx-safety-video.acfun.cn/mediacloud/acfun/hls_1080p_6m_1.m3u8?pkey=AAA"
M3U8_A_BAK = "https://ali-safety-video.acfun.cn/mediacloud/acfun/hls_1080p_6m_1.m3u8?pkey=AAA"
M3U8_B = "https://tx-safety-video.acfun.cn/mediacloud/acfun/hls_1080p_2.m3u8?pkey=BBB"
M3U8_C = "https://tx-safety-video.acfun.cn/mediacloud/acfun/hls_720p_2.m3u8?pkey=CCC"


def ks_play_json() -> str:
    return json.dumps(
        {
            "version": "1.0.0",
            "businessType": 1,
            "mediaType": 1,
            "videoId": "5f2ee5964c6b0904",
            "adaptationSet": [
                {
                    "id": 0,
                    "duration": 297920,
                    "representation": [
                        {"id": 1, "url": M3U8_A, "backupUrl": [M3U8_A_BAK], "qualityLabel": "1080P+"},
                        {"id": 2, "url": M3U8_B, "backupUrl": [], "qualityLabel": "1080P"},
                        {"id": 3, "url": M3U8_C, "backupUrl": [], "qualityLabel": "720P"},
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


def video_info() -> dict:
    return {
        "title": TITLE,
        "coverUrl": COVER,
        "currentVideoId": 39196578,
        "currentVideoInfo": {
            "id": 39196578,
            "title": TITLE,
            "ksPlayJson": ks_play_json(),
            "ksPlayJsonHevc": "{}",
        },
    }


def page_html(info: dict | None = None) -> str:
    data = json.dumps(info if info is not None else video_info(), ensure_ascii=False)
    return (
        "<html><head><script>"
        f"window.pageInfo = window.videoInfo = {data};"
        "</script></head><body></body></html>"
    )


def _transport(html: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


async def _parse(html: str, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(html, status)) as c:
        return await acfun.parse(SHARE, client=c)


# ---- 纯函数 ----


def test_extract_video_info():
    info = acfun.extract_video_info(page_html())
    assert info is not None
    assert info["title"] == TITLE
    assert info["currentVideoInfo"]["ksPlayJson"]


def test_extract_video_info_missing():
    assert acfun.extract_video_info("<html><body>no data</body></html>") is None


def test_extract_work_variants():
    work = acfun.extract_work(video_info())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == TITLE
    assert work.cover_url == COVER
    assert [v.label for v in work.variants] == ["1080P+", "1080P", "720P"]
    assert [v.rank for v in work.variants] == [0, 1, 2]
    assert work.video_url == M3U8_A
    assert work.video_fallbacks == [M3U8_A_BAK]


def test_extract_work_no_play_json():
    info = video_info()
    info["currentVideoInfo"]["ksPlayJson"] = ""
    with pytest.raises(ParseError) as exc:
        acfun.extract_work(info)
    assert exc.value.code == "WORK_UNAVAILABLE"


def test_title_default_when_missing():
    info = video_info()
    info["title"] = ""
    assert acfun.extract_work(info).title == acfun.DEFAULT_TITLE


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "acfun"), None)
    assert platform is not None
    assert platform.name == "A站"
    assert "acfun.cn" in platform.hosts
    assert "video" in platform.kinds
    assert platform.signed_media is True
    assert is_acfun_url("https://www.acfun.cn/v/ac48851115") is True
    assert is_acfun_url("https://acfun.cn.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {SHARE}")
    assert key == "acfun"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(page_html()))
    assert work.type == "video"
    assert work.video_url == M3U8_A
    assert len(work.variants) == 3


def test_parse_page_without_video_info_raises():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse("<html><body>empty</body></html>"))
    assert exc.value.code == "PARSE_FAILED"
