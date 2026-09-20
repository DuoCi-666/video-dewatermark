"""今日头条（toutiao.com）解析器测试。

全部离线：用 httpx.MockTransport 返回微头条图文页的 HTML（含 RENDER_DATA），
结构取自 2026-09 真实分享链接的抓取快照（微头条 id=1875940419961931）。

短视频页（/video/<id>/）在网页端不提供真实视频流，应报 VIDEO_IN_APP_ONLY。
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import httpx
import pytest

from app.errors import ParseError
from app.parsers import toutiao
from app.parsers.extract import extract_share_url, is_toutiao_url
from app.parsers.kuaishou import ParsedWork

SHARE = "https://m.toutiao.com/is/5RkxeMfbY5Q/"
W_PAGE = "https://m.toutiao.com/w/1875940419961931/?app=news_article"
VIDEO_PAGE = "https://m.toutiao.com/video/7639670445713799333/?app=news_article"
SHORT_VIDEO = "https://m.toutiao.com/is/MFmOOXsOE5c/"

IMG1 = (
    "https://p3-sign.toutiaoimg.com/tos-cn-i-ezhpy3drpa/"
    "d70e3beaa72e40c7941b6a83294530f1~tplv-shrink:1280:2173.jpeg"
    "?_iz=97245&bid=15&from=post&gid=1875940419961931&lk3s=06827d14"
    "&x-expires=1797292800&x-signature=AAA"
)
IMG2 = (
    "https://p11-sign.toutiaoimg.com/tos-cn-i-ezhpy3drpa/"
    "8721f466109a4fa1945e48cfe9ae0f24~tplv-shrink:1280:871.jpeg"
    "?_iz=97245&bid=15&from=post&gid=1875940419961931&lk3s=06827d14"
    "&x-expires=1797292800&x-signature=BBB"
)
TITLE = "lpl女主持人骆歆COS 紫色丝袜大长腿 紫色宝石细高跟[爱慕]#骆歆#"
AUTHOR = "我是脚印啊"


def render_data() -> dict:
    return {
        "articleInfo": {
            "thread": {
                "threadBase": {
                    "threadId": 1875940419961931,
                    "title": TITLE,
                    "content": TITLE,
                    "richContent": "lpl女主持人骆歆COS<i class=\"emoji emoji_2_kiss\"></i>"
                    "<span class=\"emoji-name\">[爱慕]</span>#骆歆#",
                    "imageCount": 2,
                    "largeImageList": [
                        {
                            "url": IMG1,
                            "width": 1280,
                            "height": 2173,
                            "urlList": [{"url": IMG1}],
                            "type": 1,
                        },
                        {
                            "url": IMG2,
                            "width": 1280,
                            "height": 871,
                            "urlList": [{"url": IMG2}],
                            "type": 1,
                        },
                    ],
                    "thumbImageList": [
                        {"url": "https://p3-sign.toutiaoimg.com/x~tplv-tt-post:400:400.jpeg"}
                    ],
                    "user": {"info": {"userId": 1, "name": AUTHOR}},
                }
            }
        }
    }


def page_html(data: dict | None, encoded: bool = True) -> str:
    payload = json.dumps(data if data is not None else render_data(), ensure_ascii=False)
    if encoded:
        payload = quote(payload)
    return (
        "<html><body>"
        f'<script id="RENDER_DATA" type="application/json">{payload}</script>'
        "</body></html>"
    )


def _page_transport(html: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


async def _parse(html: str, url: str = SHARE, status: int = 200):
    async with httpx.AsyncClient(transport=_page_transport(html, status)) as c:
        return await toutiao.parse(url, client=c)


# ---- 纯函数 ----


def test_path_id_extraction():
    assert toutiao._path_id("https://m.toutiao.com/w/1875940419961931/?app=x") == "1875940419961931"
    assert toutiao._path_id("https://www.toutiao.com/article/7639670445713799333/") == "7639670445713799333"
    assert toutiao._path_id("https://m.toutiao.com/video/7639670445713799333/") == "7639670445713799333"
    assert toutiao._path_id("https://m.toutiao.com/nope/abc") is None


def test_is_video_url():
    assert toutiao._is_video_url("https://m.toutiao.com/video/123456/") is True
    assert toutiao._is_video_url("https://www.toutiao.com/video/7639670445713799333/") is True
    assert toutiao._is_video_url("https://m.toutiao.com/w/1875940419961931/") is False


def test_decode_render_data_urlencoded():
    data = toutiao._decode_render_data(page_html(render_data(), encoded=True))
    assert data is not None
    assert data["articleInfo"]["thread"]["threadBase"]["title"] == TITLE


def test_decode_render_data_plain():
    data = toutiao._decode_render_data(page_html(render_data(), encoded=False))
    assert data is not None


def test_decode_render_data_missing():
    assert toutiao._decode_render_data("<html><body>no data</body></html>") is None


def test_strip_html():
    out = toutiao._strip_html(
        'foo<i class="emoji emoji_2_kiss"></i><span class="emoji-name">[爱慕]</span>'
        '<a href="x">#bar#</a>'
    )
    assert out == "foo#bar#"


def test_image_work_extraction():
    work = toutiao.extract_work(render_data())
    assert isinstance(work, ParsedWork)
    assert work.type == "images"
    assert work.title == TITLE
    assert work.author == AUTHOR
    assert work.image_urls == [IMG1, IMG2]
    assert work.cover_url == IMG1
    assert len(work.image_groups) == 2


def test_author_default_when_missing():
    data = render_data()
    data["articleInfo"]["thread"]["threadBase"]["user"] = {}
    assert toutiao.extract_work(data).author == toutiao.DEFAULT_AUTHOR


def test_title_falls_back_to_rich_content():
    data = render_data()
    base = data["articleInfo"]["thread"]["threadBase"]
    base["title"] = ""
    base["content"] = ""
    assert toutiao.extract_work(data).title == "lpl女主持人骆歆COS#骆歆#"


def test_title_default_when_all_empty():
    data = render_data()
    base = data["articleInfo"]["thread"]["threadBase"]
    base["title"] = ""
    base["content"] = ""
    base["richContent"] = ""
    assert toutiao.extract_work(data).title == toutiao.DEFAULT_TITLE


def test_no_images_raises_work_unavailable():
    data = render_data()
    data["articleInfo"]["thread"]["threadBase"]["largeImageList"] = []
    with pytest.raises(ParseError) as info:
        toutiao.extract_work(data)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_missing_article_info_raises_work_unavailable():
    with pytest.raises(ParseError) as info:
        toutiao.extract_work({})
    assert info.value.code == "WORK_UNAVAILABLE"


def test_image_url_dedup_and_urllist_fallback():
    data = render_data()
    base = data["articleInfo"]["thread"]["threadBase"]
    base["largeImageList"][0]["url"] = ""  # 主 url 缺失，回退 urlList
    work = toutiao.extract_work(data)
    assert work.image_urls == [IMG1, IMG2]


def test_platform_registered():
    from app.parsers.extract import PLATFORMS

    platform = next((p for p in PLATFORMS if p.key == "toutiao"), None)
    assert platform is not None
    assert platform.name == "今日头条"
    assert "toutiao.com" in platform.hosts
    assert "images" in platform.kinds
    assert platform.signed_media is True
    assert is_toutiao_url("https://m.toutiao.com/w/1") is True
    assert is_toutiao_url("https://www.toutiao.com/video/1") is True
    assert is_toutiao_url("https://m.toutiao.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看这个 {SHARE}")
    assert key == "toutiao"
    assert url == SHARE


# ---- 端到端 ----


def test_parse_images_end_to_end():
    work = asyncio.run(_parse(page_html(render_data())))
    assert work.type == "images"
    assert work.author == AUTHOR
    assert len(work.image_urls) == 2


def test_parse_video_page_raises_in_app_only():
    """短视频页：报 WORK_UNAVAILABLE 且文案提示 App 内观看。"""
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            # 短链 302 跳到 /video/<id>/
            if "/is/" in str(request.url):
                return httpx.Response(
                    302, headers={"Location": VIDEO_PAGE}
                )
            return httpx.Response(200, text=page_html(render_data()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await toutiao.parse(SHORT_VIDEO, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "WORK_UNAVAILABLE"
    assert "App 内" in info.value.message


def test_parse_404_raises_work_unavailable():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await toutiao.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "WORK_UNAVAILABLE"


def test_parse_page_without_render_data_raises_work_unavailable():
    with pytest.raises(ParseError) as info:
        asyncio.run(_parse("<html><body>empty</body></html>"))
    assert info.value.code == "WORK_UNAVAILABLE"
