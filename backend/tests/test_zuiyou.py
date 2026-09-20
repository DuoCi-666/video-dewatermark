"""最右（xiaochuankeji.cn）解析器测试。

全部离线：用 httpx.MockTransport 返回 share page 的 SSR HTML（内嵌
``window.APP_INITIAL_STATE``），结构取自 2026-09 三条真实分享链接的抓取快照
（视频帖 / 图集帖 / 纯文字帖）。

注意：最右 ≠ 皮皮搞笑（ippzone.com），是两个不同平台。
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from app.errors import ParseError
from app.parsers import zuiyou
from app.parsers.extract import extract_share_url, is_zuiyou_url
from app.parsers.kuaishou import ParsedWork

SHARE = (
    "https://share.xiaochuankeji.cn/hybrid/share/post?"
    "pid=421557374&zy_to=applink&share_count=1&m=ca63f1c7&app=zuiyou"
)
VIDEO_PID = "420880003"
IMAGE_PID = "421557374"

# 视频直链 / 图集原图（多 CDN），auth_key 为抓包时的签名样例
VIDEO = "https://web-v01.izuiyou.com/zyvqwz/264/20/65/376b-4f41-11f1-8a16-9614fd0e2995?auth_key=abc"
IMG1 = "https://web-f01.izuiyou.com/img/view/id/2536863458/sz/src?auth_key=abc"
IMG1_CDN = "https://web-f02.izuiyou.com/img/view/id/2536863458/sz/src?auth_key=abc"
IMG2 = "https://web-f01.izuiyou.com/img/view/id/2536863459/sz/src?auth_key=def"


def _img_meta(img_id: int, *, video: bool = False, with_origin: bool = True) -> dict:
    urls: dict = {
        "360": {"urls": [f"http://web-f01.izuiyou.com/img/view/id/{img_id}/sz/360"]},
        "540": {"urls": [f"http://web-f01.izuiyou.com/img/view/id/{img_id}/sz/540"]},
    }
    if with_origin:
        urls["origin"] = {"urls": [f"http://web-f01.izuiyou.com/img/view/id/{img_id}/sz/src?auth_key=x"]}
    entry: dict = {"id": img_id, "h": 2275, "w": 1280, "fmt": "jpeg", "urls": urls}
    if video:
        entry["video"] = 1
    return entry


def _image_post_data() -> dict:
    return {
        "id": 421557374,
        "content": "#哩小莹  #雪女 #cos",
        "member": {"id": 262095806, "name": "千人之律者"},
        "imgs": [
            {
                "id": 2536863458,
                "h": 2275,
                "w": 1280,
                "fmt": "jpeg",
                "urls": {
                    "540": {"urls": [IMG1.replace("sz/src", "sz/540")]},
                    "origin": {"urls": [IMG1, IMG1_CDN]},
                },
            },
            {
                "id": 2536863459,
                "h": 2275,
                "w": 1280,
                "fmt": "jpeg",
                "urls": {"origin": {"urls": [IMG2]}},
            },
        ],
    }


def _video_post_data() -> dict:
    return {
        "id": 420880003,
        "content": "和朋友说好一起cos猴子的，背刺我！",
        "member": {"id": 123, "name": "派大星的海洋裤"},
        "imgs": [_img_meta(2534734879, video=True, with_origin=False)],
        "videos": {
            "2534734879": {"dur": 9, "url": f"http://{VIDEO[len('https://'):]}"},
        },
    }


def _render(post: dict | None, failure: dict | None = None) -> str:
    share_post: dict = {"postDetail": {"post": post} if post else {}, "postFailure": failure or {}}
    state = {"sharePost": share_post}
    raw = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    return (
        '<html><head></head><body>'
        f'<script id="appState">window.APP_INITIAL_STATE={raw};</script>'
        "</body></html>"
    )


def _transport(html: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


async def _parse(post: dict | None, failure: dict | None = None, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(_render(post, failure), status)) as c:
        return await zuiyou.parse(SHARE, client=c)


# ---- 链接识别 ----


def test_pid_extraction():
    assert zuiyou._pid_from(SHARE) == "421557374"
    assert zuiyou._pid_from("https://share.xiaochuankeji.cn/hybrid/share/post?pid=420880003&app=zuiyou") == "420880003"
    assert zuiyou._pid_from("https://h5.ippzone.com/spacey/post/123") is None
    assert zuiyou._pid_from("https://share.xiaochuankeji.cn/hybrid/share/post?app=zuiyou") is None


def test_platform_registered():
    from app.parsers.extract import PLATFORMS

    platform = next((p for p in PLATFORMS if p.key == "zuiyou"), None)
    assert platform is not None
    assert platform.name == "最右"
    assert "xiaochuankeji.cn" in platform.hosts
    assert platform.signed_media is True
    assert is_zuiyou_url("https://share.xiaochuankeji.cn/hybrid/share/post?pid=1") is True
    assert is_zuiyou_url("https://xiaochuankeji.cn.evil.com/x") is False
    assert is_zuiyou_url("https://h5.ippzone.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"#最右#分享一条有趣的内容给你。请戳链接>> {SHARE}")
    assert key == "zuiyou"
    assert url == SHARE


def test_zuiyou_and_pipigaoxiao_are_distinct():
    """最右与皮皮搞笑（都是最右系）不能被互相识别错。"""
    from app.parsers.extract import extract_share_url as e

    assert e("https://share.xiaochuankeji.cn/hybrid/share/post?pid=1")[0] == "zuiyou"
    assert e("https://h5.ippzone.com/spacey/post/123")[0] == "pipigaoxiao"


# ---- 纯函数 ----


def test_https_upgrade():
    assert zuiyou._to_https("http://web-v01.izuiyou.com/a?b=1") == "https://web-v01.izuiyou.com/a?b=1"
    assert zuiyou._to_https("https://x.com/a") == "https://x.com/a"
    assert zuiyou._to_https("not-a-url") == "not-a-url"


def test_video_work_extraction():
    work = zuiyou.extract_work({"postDetail": {"post": _video_post_data()}})
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == "和朋友说好一起cos猴子的，背刺我！"
    assert work.author == "派大星的海洋裤"
    assert work.video_url == VIDEO
    assert work.video_fallbacks == []
    assert work.variants == []


def test_video_cover_taken_from_first_frame_image():
    """视频帖里 imgs[] 的 video:1 图（首帧）应作为封面而不是图集内容。"""
    post = _video_post_data()
    post["imgs"][0]["urls"]["540"] = {"urls": [post["imgs"][0]["urls"]["540"]["urls"][0]]}
    work = zuiyou.extract_work({"postDetail": {"post": post}})
    assert work.type == "video"
    assert work.cover_url is not None and "2534734879" in work.cover_url
    assert work.image_urls == []


def test_image_work_extraction():
    work = zuiyou.extract_work({"postDetail": {"post": _image_post_data()}})
    assert work.type == "images"
    assert work.author == "千人之律者"
    assert work.image_urls == [IMG1, IMG2]
    assert work.image_groups[0] == [IMG1, IMG1_CDN]
    assert len(work.image_groups) == 2


def test_image_first_frame_skipped():
    """图集帖里带 video:1 戳的首帧图不进入图集、也不当封面。"""
    empty_first: dict = {
        "id": 999,
        "h": 2000,
        "w": 1000,
        "video": 1,
        "fmt": "jpeg",
        "urls": {"540": {"urls": ["http://web-f01.izuiyou.com/img/view/id/999/sz/540"]}},
    }
    data = _image_post_data()
    data["imgs"] = [empty_first, *_image_post_data()["imgs"]]
    work = zuiyou.extract_work({"postDetail": {"post": data}})
    assert work.type == "images"
    assert all("999" not in (u or "") for group in work.image_groups for u in group)
    assert work.image_urls == [IMG1, IMG2]


def test_author_default_when_missing():
    data = _image_post_data()
    del data["member"]
    assert zuiyou.extract_work({"postDetail": {"post": data}}).author == zuiyou.DEFAULT_AUTHOR


def test_title_default_when_empty():
    data = _video_post_data()
    data["content"] = ""
    assert zuiyou.extract_work({"postDetail": {"post": data}}).title == zuiyou.DEFAULT_TITLE


def test_text_only_post_raises_work_unavailable():
    """纯文字帖（无 videos 无图集）应报作品不可用。"""
    post = {
        "id": 421557375,
        "content": "到底是谁传的年轻人都会修路由器",
        "member": {"name": "张三"},
        "imgs": [],
    }
    with pytest.raises(ParseError) as info:
        zuiyou.extract_work({"postDetail": {"post": post}})
    assert info.value.code == "WORK_UNAVAILABLE"


def test_failure_maps_to_work_unavailable():
    with pytest.raises(ParseError) as info:
        zuiyou.extract_work({"postFailure": {"ret": -18, "msg": "帖子不存在"}})
    assert info.value.code == "WORK_UNAVAILABLE"


def test_missing_share_post_raises_work_unavailable():
    with pytest.raises(ParseError) as info:
        zuiyou.extract_work({})
    assert info.value.code == "WORK_UNAVAILABLE"


# ---- 端到端 ----


def test_parse_video_end_to_end():
    work = asyncio.run(_parse(_video_post_data()))
    assert work.type == "video"
    assert work.video_url == VIDEO


def test_parse_images_end_to_end():
    work = asyncio.run(_parse(_image_post_data()))
    assert work.type == "images"
    assert len(work.image_urls) == 2


def test_parse_404_maps_to_work_unavailable():
    async def run():
        async with httpx.AsyncClient(transport=_transport(_render(None), 404)) as c:
            return await zuiyou.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "WORK_UNAVAILABLE"


def test_parse_missing_state_maps_to_parse_failed():
    html = "<html><body>hello</body></html>"

    async def run():
        async with httpx.AsyncClient(transport=_transport(html)) as c:
            return await zuiyou.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "PARSE_FAILED"


def test_parse_risk_page_maps_to_risk_control():
    async def run():
        async with httpx.AsyncClient(transport=_transport("<html>访问太频繁</html>", 200)) as c:
            return await zuiyou.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "RISK_CONTROL"


def test_app_state_regex_handles_escaped_html():
    r"""APP_INITIAL_STATE 里含 `</` 时 JSON 会转义为 `\/`，正则要能正确截取整段。"""
    post = _video_post_data()
    state = {"sharePost": {"postDetail": {"post": post}}}
    raw = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    html = f'<script id="appState">window.APP_INITIAL_STATE={raw};</script>'
    decoded = zuiyou._decode_app_state(html)
    assert decoded is not None
    assert zuiyou.extract_work(decoded["sharePost"]).video_url == VIDEO