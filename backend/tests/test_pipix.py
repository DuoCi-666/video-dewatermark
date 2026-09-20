"""皮皮虾（pipix.com）解析器测试。

全部离线：用 httpx.MockTransport 返回「302 短链 + RENDER_DATA 页面」，
不依赖真实网络。结构取自 2026-09 两条真实分享链接的抓取快照。
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import httpx
import pytest

from app.errors import ParseError
from app.parsers import pipix
from app.parsers.extract import extract_share_url, is_pipix_url
from app.parsers.kuaishou import ParsedWork

SHARE_SHORT = "https://h5.pipix.com/s/DisKWKo118I/"
SHARE_LONG = "https://h5.pipix.com/ppx/item/7218192909349165349?app_id=1319&app=super"
VIDEO_ID = "7218192909349165349"
IMAGE_ID = "6889634935628372236"

VIDEO_URL = "https://v26-cdn-tos.ppxvod.com/de9d/video/tos/cn/origin.mp4?a=1319&l=2026"
VIDEO_URL_2 = "https://v3-cdn-tos.ppxvod.com/cf51/video/tos/cn/origin.mp4?a=1319&l=2026"
COVER = "https://p3-ppx-sign.byteimg.com/tos-cn-p-0076/cover~tplv-noop.jpeg?x-expires=1821070551"
IMAGE_URL = "https://p6-ppx-sign.byteimg.com/tos-cn-i-0000/img0~tplv-noop-v4:1125:1500:q60.jpeg?x-expires=1"
IMAGE_URL_2 = "https://p6-ppx-sign.byteimg.com/tos-cn-i-0000/img1~tplv-noop-v4:1125:1500:q60.jpeg?x-expires=1"
IMAGE_URL_3 = "https://p6-ppx-sign.byteimg.com/tos-cn-i-0000/img2~tplv-noop-v4:1125:1500:q60.jpeg?x-expires=1"


def video_item() -> dict:
    return {
        "item_id_str": VIDEO_ID,
        "item_type": 2,
        "content": "cos",
        "cover": {"url_list": [{"url": COVER}]},
        "share": {"title": "cos", "large_image_url": COVER},
        "author": {"id_str": "4081007519340103", "name": "谢霄霄"},
        "video": {
            "video_id": "v0d04cg10000cgm2ocbc77ucdqd2gbi0",
            "video_download": {
                "url_list": [{"url": VIDEO_URL, "expires": 604800}, {"url": VIDEO_URL_2, "expires": 604800}],
                "definition": 4,
            },
            "cover_image": {"url_list": [{"url": COVER}]},
            "video_high": None,
            "video_mid": None,
            "video_low": None,
            "duration": 10.897,
        },
    }


def image_item() -> dict:
    return {
        "item_id_str": IMAGE_ID,
        "item_type": 1,
        "content": "cos",
        "cover": {"url_list": [{"url": COVER}]},
        "share": {"title": "cos"},
        "author": {"id_str": "1", "name": "十年怎么走"},
        "video": None,
        "note": {
            "multi_image": [
                {"width": 1125, "height": 1500, "url_list": [{"url": IMAGE_URL}]},
                {"width": 1125, "height": 1500, "url_list": [{"url": IMAGE_URL_2}]},
                {"width": 1125, "height": 1500, "url_list": [{"url": IMAGE_URL_3}]},
            ],
        },
    }


def render_html(item: dict, encoded: bool = True) -> str:
    payload = json.dumps(
        {"ppxItemDetail": {"item": item}}, ensure_ascii=False, separators=(",", ":")
    )
    body = quote(payload, safe="") if encoded else payload
    return (
        "<!doctype html><html><head><title>皮皮虾</title></head><body>"
        f'<script id="RENDER_DATA" type="application/json">{body}</script>'
        "</body></html>"
    )


# ---- 纯函数 ----


def test_item_id_from_urls():
    assert pipix._item_id_from(SHARE_LONG) == VIDEO_ID
    assert pipix._item_id_from(SHARE_SHORT) is None
    assert pipix._item_id_from_location(f"https://h5.pipix.com/ppx/item/{VIDEO_ID}?a=1") == VIDEO_ID
    assert pipix._item_id_from_location("") is None


def test_extract_render_data_urlencoded_and_raw():
    html = render_html(video_item(), encoded=True)
    data = pipix._extract_render_data(html)
    assert data is not None and "ppxItemDetail" in data
    raw_html = render_html(video_item(), encoded=False)
    assert pipix._extract_render_data(raw_html) is not None
    assert pipix._extract_render_data("<html>no data</html>") is None


def test_video_work_extraction():
    work = pipix.extract_work(video_item())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.author == "谢霄霄"
    assert work.cover_url == COVER
    assert work.video_url == VIDEO_URL
    assert work.video_fallbacks == [VIDEO_URL_2]
    assert [(v.label, v.rank) for v in work.variants] == [("默认", 0)]
    assert work.variants[0].urls == [VIDEO_URL, VIDEO_URL_2]


def test_image_work_extraction():
    work = pipix.extract_work(image_item())
    assert work.type == "images"
    assert work.author == "十年怎么走"
    assert work.image_urls == [IMAGE_URL, IMAGE_URL_2, IMAGE_URL_3]
    assert [len(g) for g in work.image_groups] == [1, 1, 1]
    assert work.image_groups[0][0] == IMAGE_URL


def test_title_fallback_chain():
    item = video_item()
    item["content"] = "标题A"
    assert pipix.extract_work(item).title == "标题A"
    item["content"] = ""
    assert pipix.extract_work(item).title == "cos"  # share.title
    item["share"]["title"] = ""
    assert pipix.extract_work(item).title == pipix.DEFAULT_TITLE


def test_author_default_when_missing():
    item = video_item()
    item.pop("author")
    assert pipix.extract_work(item).author == pipix.DEFAULT_AUTHOR


def test_no_media_raises_work_unavailable():
    item = video_item()
    item["video"]["video_download"] = {"url_list": []}
    with pytest.raises(ParseError) as info:
        pipix.extract_work(item)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_platform_registered_as_signed_media():
    from app.parsers.extract import PLATFORMS, signed_media_platforms

    platform = next((p for p in PLATFORMS if p.key == "pipix"), None)
    assert platform is not None
    assert platform.name == "皮皮虾"
    assert "pipix.com" in platform.hosts
    assert is_pipix_url("https://h5.pipix.com/s/abc/") is True
    assert is_pipix_url("https://h5.pipix.com.evil.com/x") is False
    assert "pipix" in signed_media_platforms()


def test_extract_share_url_pipix():
    key, url = extract_share_url(f"看看这个 {SHARE_SHORT}")
    assert key == "pipix"
    assert url == SHARE_SHORT
    assert extract_share_url(SHARE_LONG)[0] == "pipix"


def test_refresh_info_enabled_for_pipix():
    from app.main import _refresh_info

    info = _refresh_info("pipix", SHARE_SHORT, "video")
    assert info == {"platform": "pipix", "share_url": SHARE_SHORT, "kind": "video", "index": 0}


# ---- 端到端（MockTransport 驱动 parse）----


def _transport_short(html: str, location: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/s/" in str(request.url):
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


async def _parse_with_transport(transport: httpx.MockTransport):
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as c:
        return await pipix.parse(SHARE_SHORT, client=c)


def test_parse_short_link_video_end_to_end():
    transport = _transport_short(render_html(video_item()), f"https://h5.pipix.com/ppx/item/{VIDEO_ID}?a=1")
    work = asyncio.run(_parse_with_transport(transport))
    assert work.type == "video"
    assert work.video_url == VIDEO_URL
    assert work.author == "谢霄霄"


def test_parse_short_link_images_end_to_end():
    transport = _transport_short(render_html(image_item()), f"https://h5.pipix.com/ppx/item/{IMAGE_ID}?a=1")
    work = asyncio.run(_parse_with_transport(transport))
    assert work.type == "images"
    assert len(work.image_urls) == 3


def test_parse_no_render_data_maps_to_parse_failed():
    transport = _transport_short("<html><body>empty</body></html>", f"https://h5.pipix.com/ppx/item/{VIDEO_ID}")
    with pytest.raises(ParseError) as info:
        asyncio.run(_parse_with_transport(transport))
    assert info.value.code == "PARSE_FAILED"


def test_parse_404_maps_to_work_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/s/" in str(request.url):
            return httpx.Response(302, headers={"location": f"https://h5.pipix.com/ppx/item/{VIDEO_ID}"})
        return httpx.Response(404, text="not found")

    with pytest.raises(ParseError) as info:
        asyncio.run(_parse_with_transport(httpx.MockTransport(handler)))
    assert info.value.code == "WORK_UNAVAILABLE"
