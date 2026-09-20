"""皮皮搞笑（ippzone.com）解析器测试。

全部离线：用 httpx.MockTransport 返回 fetch_content 接口的样例响应，
结构取自 2026-09 两条真实分享链接的抓取快照。

注意：皮皮搞笑 ≠ 皮皮虾（pipix.com），是「最右」旗下 site。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.errors import ParseError
from app.parsers import pipigaoxiao
from app.parsers.extract import extract_share_url, is_pipigaoxiao_url
from app.parsers.kuaishou import ParsedWork

SHARE = (
    "https://h5.ippzone.com/spacey/post/884630231630"
    "?zy_to=copy_link&app=&type=post&mid=8666254324736&pid=884630231630"
)
VIDEO_PID = "887666955346"
IMAGE_PID = "884630231630"

VIDEO_A = "http://video03.szsttkj.com/zyvqwz/264/6a/94/origin"
VIDEO_B = "http://video01.szsttkj.com/zyvqwz/264/6a/94/origin"
VIDEO_C = "http://xlal-ppvideo.szsttkj.com/zyvqwz/264/6a/94/origin"
COVER = "http://file.ippzone.com/img/frame/id/2535088835?w=540&delogo=0"
IMG1 = "http://file.ippzone.com/img/view/id/2531034027/sz/src"
IMG1_CDN = "http://bd-file.ippzone.com/img/view/id/2531034027/sz/src"
IMG2 = "http://file.ippzone.com/img/view/id/2531034028/sz/src"


def image_data() -> dict:
    return {
        "god_review_cnt": 4,
        "post": {
            "_id": 884630231630,
            "id": 884630231630,
            "mid": 8236556227462,
            "content": "6000块拍的cosplay，大家能看出来我cos哪个角色吗？？",
            "imgs": [
                {
                    "id": 2531034027,
                    "w": 1125,
                    "h": 1687,
                    "fmt": "webp",
                    "urls": {
                        "540": {"urls": ["http://file.ippzone.com/img/view/id/2531034027/sz/540"]},
                        "origin": {"urls": [IMG1, IMG1_CDN]},
                    },
                },
                {
                    "id": 2531034028,
                    "w": 1125,
                    "h": 1687,
                    "fmt": "webp",
                    "urls": {"origin": {"urls": [IMG2]}},
                },
            ],
        },
        "member": {"id": 85798195, "name": "隔壁王大爷....."},
    }


def video_data() -> dict:
    return {
        "god_review_cnt": 0,
        "post": {
            "_id": 887666955346,
            "id": 887666955346,
            "mid": 7741829734499,
            "content": "cos",
            "imgs": [{"id": 2535088835, "video": 1, "urls": {}}],
            "videos": {
                "2535088835": {
                    "dur": 8,
                    "url": VIDEO_B,
                    "urlsrc": VIDEO_B,
                    "urlwm": VIDEO_B,
                    "cover_urls": [COVER],
                    "qualities": [
                        {
                            "resolution": 540,
                            "urls": [{"url": VIDEO_A}, {"url": VIDEO_B}, {"url": VIDEO_C}],
                        }
                    ],
                }
            },
        },
        "member": {"id": 123, "name": "血出公子"},
    }


def _transport(data: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/share/fetch_content" in str(request.url):
            return httpx.Response(status, json={"ret": 1, "data": data})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _parse(data: dict, status: int = 200):
    async with httpx.AsyncClient(transport=_transport(data, status)) as c:
        return await pipigaoxiao.parse(SHARE, client=c)


# ---- 纯函数 ----


def test_pid_extraction():
    assert pipigaoxiao._pid_from(SHARE) == "884630231630"
    assert pipigaoxiao._pid_from("https://h5.ippzone.com/spacey/post/887666955346") == "887666955346"
    assert pipigaoxiao._pid_from("https://h5.ippzone.com/x?pid=123") == "123"
    assert pipigaoxiao._pid_from("https://h5.ippzone.com/spacey/no/abc") is None


def test_req_body_has_required_fields():
    body = json.loads(pipigaoxiao._req_body("884630231630"))
    assert body["h_app"] == "zuiyou_lite"
    assert body["pid"] == 884630231630
    assert body["type"] == "post"
    assert body["h_dt"] == 1
    assert body["in_app"] is False
    assert isinstance(body["h_ts"], int)
    assert "ua" in body and body["ua"].startswith("Mozilla")


def test_video_work_extraction():
    work = pipigaoxiao.extract_work(video_data())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == "cos"
    assert work.author == "血出公子"
    assert work.cover_url == COVER
    assert work.video_url == VIDEO_A
    assert work.video_fallbacks == [VIDEO_B, VIDEO_C]
    assert [(v.label, v.rank) for v in work.variants] == [("540", 0)]
    assert work.variants[0].urls == [VIDEO_A, VIDEO_B, VIDEO_C]


def test_image_work_extraction():
    work = pipigaoxiao.extract_work(image_data())
    assert work.type == "images"
    assert work.author == "隔壁王大爷....."
    assert work.image_urls == [IMG1, IMG2]
    assert work.image_groups[0] == [IMG1, IMG1_CDN]
    assert len(work.image_groups) == 2


def test_author_default_when_missing():
    data = video_data()
    data.pop("member")
    assert pipigaoxiao.extract_work(data).author == pipigaoxiao.DEFAULT_AUTHOR


def test_title_default_when_empty():
    data = video_data()
    data["post"]["content"] = ""
    assert pipigaoxiao.extract_work(data).title == pipigaoxiao.DEFAULT_TITLE


def test_no_media_raises_work_unavailable():
    data = video_data()
    data["post"]["videos"] = {}
    data["post"]["imgs"] = []
    with pytest.raises(ParseError) as info:
        pipigaoxiao.extract_work(data)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_video_without_qualities_falls_back_to_url():
    data = video_data()
    v = data["post"]["videos"]["2535088835"]
    v.pop("qualities")
    work = pipigaoxiao.extract_work(data)
    assert work.type == "video"
    assert work.video_url == VIDEO_B


def test_platform_registered():
    from app.parsers.extract import PLATFORMS

    platform = next((p for p in PLATFORMS if p.key == "pipigaoxiao"), None)
    assert platform is not None
    assert platform.name == "皮皮搞笑"
    assert "ippzone.com" in platform.hosts
    assert is_pipigaoxiao_url("https://h5.ippzone.com/spacey/post/1") is True
    assert is_pipigaoxiao_url("https://h5.ippzone.com.evil.com/x") is False


def test_extract_share_url():
    key, url = extract_share_url(f"看看这个 {SHARE}")
    assert key == "pipigaoxiao"
    assert url == SHARE


def test_pipix_and_pipigaoxiao_are_distinct():
    """两个「皮皮」平台不能互相识别错。"""
    from app.parsers.extract import extract_share_url as e

    assert e("https://h5.pipix.com/s/abc/")[0] == "pipix"
    assert e("https://h5.ippzone.com/spacey/post/123")[0] == "pipigaoxiao"


# ---- 端到端 ----


def test_parse_video_end_to_end():
    work = asyncio.run(_parse(video_data()))
    assert work.type == "video"
    assert work.video_url == VIDEO_A


def test_parse_images_end_to_end():
    work = asyncio.run(_parse(image_data()))
    assert work.type == "images"
    assert len(work.image_urls) == 2


def test_parse_ret_error_maps_to_parse_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -1, "msg": "出现了一个小问题"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await pipigaoxiao.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "PARSE_FAILED"


def test_parse_deleted_maps_to_work_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -1, "msg": "作品不存在或已删除"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await pipigaoxiao.parse(SHARE, client=c)

    with pytest.raises(ParseError) as info:
        asyncio.run(run())
    assert info.value.code == "WORK_UNAVAILABLE"
