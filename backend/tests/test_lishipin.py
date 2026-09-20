"""梨视频（pearvideo.com）解析器测试。

全部离线：``videoStatus`` 结构取自 2026-09 真实响应快照（contId=1806878）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.errors import ParseError
from app.parsers import lishipin
from app.parsers.extract import PLATFORMS, extract_share_url, is_lishipin_url
from app.parsers.kuaishou import ParsedWork

CONT_ID = "1806878"
SHARE = f"https://www.pearvideo.com/detail_{CONT_ID}"
TIMER = "1789574412508"
SRC_WITH_TIMER = (
    "https://video.pearvideo.com/mp4/short/20260720/"
    f"{TIMER}-16079498-hd.mp4"
)
SRC_NO_WATERMARK = (
    "https://video.pearvideo.com/mp4/short/20260720/"
    f"cont-{CONT_ID}-16079498-hd.mp4"
)
COVER = f"https://image.pearvideo.com/cont/20260720/cont-{CONT_ID}-12804918.jpg"


def payload(result_code: str = "1", src: str = SRC_WITH_TIMER) -> dict:
    return {
        "resultCode": result_code,
        "resultMsg": "success",
        "systemTime": TIMER,
        "videoInfo": {
            "playSta": "1",
            "video_image": COVER,
            "videos": {"srcUrl": src, "hdUrl": "", "sdUrl": ""},
        },
    }


def _transport(body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def _parse(url: str, body: dict, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(body, status)) as c:
        return await lishipin.parse(url, client=c)


# ---- 纯函数 ----


def test_extract_cont_id():
    assert lishipin.extract_cont_id(SHARE) == CONT_ID
    assert lishipin.extract_cont_id(f"https://www.pearvideo.com/video_{CONT_ID}") == CONT_ID
    assert lishipin.extract_cont_id("https://www.pearvideo.com/") is None


def test_extract_work_replaces_timer():
    work = lishipin.extract_work(payload(), CONT_ID)
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.video_url == SRC_NO_WATERMARK
    assert work.cover_url == COVER


def test_extract_work_bad_result_code():
    with pytest.raises(ParseError) as exc:
        lishipin.extract_work(payload(result_code="0"), CONT_ID)
    assert exc.value.code == "PARSE_FAILED"


def test_extract_work_no_src():
    with pytest.raises(ParseError) as exc:
        lishipin.extract_work(payload(src=""), CONT_ID)
    assert exc.value.code == "WORK_UNAVAILABLE"


def test_extract_work_src_without_timer_unchanged():
    work = lishipin.extract_work(payload(src=SRC_NO_WATERMARK), CONT_ID)
    assert work.video_url == SRC_NO_WATERMARK


# ---- 注册 / 路由 ----


def test_platform_registered():
    platform = next((p for p in PLATFORMS if p.key == "lishipin"), None)
    assert platform is not None
    assert platform.name == "梨视频"
    assert "pearvideo.com" in platform.hosts
    assert "video" in platform.kinds
    assert is_lishipin_url("https://www.pearvideo.com/detail_1") is True
    assert is_lishipin_url("https://pearvideo.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看 {SHARE}")
    assert key == "lishipin"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(SHARE, payload()))
    assert work.video_url == SRC_NO_WATERMARK


def test_parse_bad_url_raises():
    with pytest.raises(ParseError) as exc:
        asyncio.run(_parse("https://www.pearvideo.com/", payload()))
    assert exc.value.code == "PARSE_FAILED"
