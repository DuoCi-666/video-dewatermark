"""腾讯微视（weishi.qq.com）解析器测试。

全部离线：用 httpx.MockTransport 模拟
1. 短链 302 -> 分享页 URL（含 id=<feedid>）
2. WSH5GetPlayPage API 的响应（结构取自 2026-09 真实链接抓包）

注意：微视是本域 weishi.qq.com，与腾讯频道（pd.qq.com）是不同平台。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.errors import ParseError
from app.parsers import weishi
from app.parsers.extract import extract_share_url, is_weishi_url
from app.parsers.kuaishou import ParsedWork

SHARE_SHORT = "https://video.weishi.qq.com/dpIibFVG"
SHARE_PAGE = (
    "https://isee.weishi.qq.com/ws/app-pages/share/index.html?"
    "wxplay=1&id=81PB80exg1X6EG6Ay&spid=8923052926331407974"
)
FEED_ID = "81PB80exg1X6EG6Ay"

# 视频直链样例（HTTP，需升 HTTPS；带短时效签名）
V0 = "http://v.weishi.qq.com/gzc_2845_1047_0b53weba4aactyapuxflxzvblmieb2yqedsa.f70.mp4?dis_k=aaa&dis_t=123&weishi_play_expire=999"
V0B = "http://v.weishi.qq.com/gzc_2845_1047_0b53weba4aactyapuxflxzvblmieb2yqedsa.f70.mp4?dis_k=bbb&dis_t=123&weishi_play_expire=999"
V45 = "http://v.weishi.qq.com/gzc_2845_1047_0b53weba4aactyapuxflxzvblmieb2yqedsa.f206313.mp4?dis_k=ccc&dis_t=123&weishi_play_expire=999"
V46 = "http://v.weishi.qq.com/gzc_2845_1047_0b53weba4aactyapuxflxzvblmieb2yqedsa.f204110.mp4?dis_k=ddd&dis_t=123&weishi_play_expire=999"
COVER = "https://xp.qpic.cn/oscar_pic/0/abc_400_1/480"


def _spec(url: str, width: int, height: int, quality: int, **kw) -> dict:
    base = {
        "url": url,
        "size": "3452397",
        "hardorsoft": 0,
        "haveWatermark": 0,
        "width": width,
        "height": height,
        "videoCoding": 1,
        "videoQuality": quality,
        "fps": 60,
    }
    base.update(kw)
    return base


def feed_data() -> dict:
    return {
        "id": FEED_ID,
        "poster": {"id": 123, "type": 0, "nick": "纸鱼"},
        "feed_desc": "真人 #游戏美女 #王者荣耀 #美女coser #cosplay",
        "video_url": "http://v.weishi.qq.com/v.weishi.qq.com/video.mp4?dis_k=xx",
        "video_cover": {"static_cover": {"url": "https://xp.qpic.cn/oscar_pic/0/abc_400_1/480"}},
        "video_spec_urls": {
            "0": _spec(V0, 720, 1280, 3),
            "1": _spec(V0B, 720, 1280, 3),
            "45": _spec(V45, 720, 1280, 2),
            "46": _spec(V46, 486, 864, 2),
            "999": _spec(V0, 720, 1280, 3),  # 重复档，不应重复收集
        },
    }


def api_response(feed: dict | None) -> dict:
    return {
        "rsp_header": {},
        "rsp_body": {"feeds": [feed] if feed else [], "isdeleted": 0, "errmsg": ""},
    }


def _transport_chain(feed: dict | None, api_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "video.weishi.qq.com" in url and request.method == "GET":
            return httpx.Response(302, headers={"location": SHARE_PAGE}, json={})
        if "isee.weishi.qq.com" in url and request.method == "GET":
            return httpx.Response(200, text="<html>spa</html>")
        if "api.weishi.qq.com" in url and "/WSH5GetPlayPage" in url:
            return httpx.Response(api_status, json=api_response(feed))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _parse(feed: dict | None, api_status: int = 200):
    async with httpx.AsyncClient(transport=_transport_chain(feed, api_status), follow_redirects=True) as c:
        return await weishi.parse(SHARE_SHORT, client=c)


# ---- 链接识别 ----


def test_platform_registered():
    from app.parsers.extract import PLATFORMS

    platform = next((p for p in PLATFORMS if p.key == "weishi"), None)
    assert platform is not None
    assert platform.name == "微视"
    assert "weishi.qq.com" in platform.hosts
    assert platform.signed_media is True
    assert platform.kinds == ("video",)
    assert is_weishi_url("https://video.weishi.qq.com/dpIibFVG") is True
    assert is_weishi_url("https://isee.weishi.qq.com/ws/app-pages/share/index.html?id=x") is True
    assert is_weishi_url("https://weishi.qq.com.evil.com/x") is False
    assert is_weishi_url("https://pd.qq.com/s/abc") is False


def test_feed_id_extraction():
    assert weishi._feed_id_from_url(SHARE_PAGE) == FEED_ID
    assert weishi._feed_id_from_url("https://isee.weishi.qq.com/x?id=abcdefg&spid=1") == "abcdefg"
    assert weishi._feed_id_from_url("https://video.weishi.qq.com/dpIibFVG") is None


def test_extract_share_url():
    key, url = extract_share_url(f"真人 #游戏美女 #王者荣耀 #美女coser #cosplay >> {SHARE_SHORT}")
    assert key == "weishi"
    assert url == SHARE_SHORT


def test_video_url_is_https_upgraded():
    assert weishi._to_https(V0) == V0.replace("http://", "https://")


# ---- 纯函数 ----


def test_video_work_extraction():
    work = weishi.extract_work(feed_data())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == "真人 #游戏美女 #王者荣耀 #美女coser #cosplay"
    assert work.author == "纸鱼"
    assert work.cover_url == COVER
    assert work.video_url == V0.replace("http://", "https://")
    assert work.video_fallbacks == [V0B.replace("http://", "https://")]
    # 三个档位（两个 1280 高分档 + 一个 864 中低档），每档多 CDN
    assert [(v.label, v.rank) for v in work.variants] == [
        ("高清 720x1280", 0),
        ("标准 720x1280", 1),
        ("标准 486x864", 2),
    ]
    assert work.variants[0].urls == [V0.replace("http://", "https://"), V0B.replace("http://", "https://")]


def test_duplicate_spec_key_998_is_deduplicated():
    """同一 URL 在不同 spec key 重复出现时只收一次。"""
    work = weishi.extract_work(feed_data())
    assert work.variants[0].urls == list(dict.fromkeys(work.variants[0].urls))


def test_watermark_spec_excluded():
    data = feed_data()
    data["video_spec_urls"]["1"] = _spec(V0B, 720, 1280, 3, haveWatermark=1)
    work = weishi.extract_work(data)
    assert V0B.replace("http://", "https://") not in work.video_fallbacks


def test_author_default_when_missing():
    data = feed_data()
    del data["poster"]
    assert weishi.extract_work(data).author == weishi.DEFAULT_AUTHOR


def test_title_default_when_empty():
    data = feed_data()
    data["feed_desc"] = ""
    assert weishi.extract_work(data).title == weishi.DEFAULT_TITLE


def test_no_media_raises_work_unavailable():
    data = feed_data()
    data["video_spec_urls"] = {}
    with pytest.raises(ParseError) as info:
        weishi.extract_work(data)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_empty_feed_raises_parse_failed():
    with pytest.raises(ParseError) as info:
        weishi.extract_work({})
    assert info.value.code == "PARSE_FAILED"


# ---- 端到端 ----


def test_parse_end_to_end():
    work = asyncio.run(_parse(feed_data()))
    assert work.type == "video"
    assert work.video_url == V0.replace("http://", "https://")
    assert work.author == "纸鱼"


def test_parse_empty_feeds_maps_to_work_unavailable():
    async def run():
        async with httpx.AsyncClient(
            transport=_transport_chain(None), follow_redirects=True
        ) as c:
            return await weishi.parse(SHARE_SHORT, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "WORK_UNAVAILABLE"


def test_parse_api_failure_maps_to_parse_failed():
    async def run():
        async with httpx.AsyncClient(
            transport=_transport_chain(feed_data(), api_status=500), follow_redirects=True
        ) as c:
            return await weishi.parse(SHARE_SHORT, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "PARSE_FAILED"


def test_parse_timeout_maps_to_parse_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await weishi.parse(SHARE_SHORT, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "PARSE_TIMEOUT"
