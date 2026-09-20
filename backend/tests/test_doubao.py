import json

import pytest
from fastapi.testclient import TestClient

from app.errors import UNSUPPORTED_PLATFORM, ParseError
from app.main import app
from app.parsers.doubao import (
    extract_thread_payload,
    is_video_sharing_url,
    parse_thread_data,
    parse_video_payload,
    video_ids_from_url,
)
from app.parsers.extract import extract_share_url

client = TestClient(app)

THREAD_SHARE = {
    "share_info": {
        "share_id": "xa1tW3szrrCdLo9OD",
        "share_name": "果冻风动漫图生成",
        "user": {"nick_name": "楉野"},
    },
    "message_snapshot": {
        "message_list": [
            {
                "content_type": 9999,
                "content": json.dumps(
                    [
                        {
                            "block_type": 2074,
                            "content": {
                                "creation_block": {
                                    "creations": [
                                        {
                                            "type": 1,
                                            "id": "55035643942935554",
                                            "image": {
                                                "image_ori_raw": {
                                                    "url": "https://p11.example/rc_gen_image/a.jpeg~tplv-image_raw.png"
                                                },
                                                "image_ori": {
                                                    "url": "https://p11.example/rc_gen_image/a.jpeg~tplv-i_dld_wm.png"
                                                },
                                                "image_thumb": {
                                                    "url": "https://p3.example/rc_gen_image/a.jpeg~tplv-thumb.png"
                                                },
                                            },
                                        }
                                    ]
                                }
                            },
                        }
                    ]
                ),
            }
        ]
    },
}

THREAD_HTML = (
    '<script data-fn-args="'
    + json.dumps(
        [
            "thread_(token)/page",
            [
                {
                    "key": "shareInfo",
                    "routerDataFnArgs": [json.dumps({"data": THREAD_SHARE})],
                }
            ],
        ]
    ).replace('"', "&quot;")
    + '"></script>'
)

VIDEO_PAYLOAD = {
    "code": 0,
    "data": {
        "play_info": {
            "main": "https://v11.example/play.mp4?lr=video_gen_watermark_dyn",
            "backup": "https://v5.example/play.mp4?lr=video_gen_watermark_dyn",
            "poster_url": "https://p3.example/cover.jpg",
            "definition": "1080p",
        },
        "user_info": {"nickname": "甜甜"},
        "prompt": "【精准提示词】\n无侵权内容\n3:4，10s",
    },
}


def test_extract_share_url_doubao():
    assert extract_share_url("https://www.doubao.com/thread/xa1tW3szrrCdLo9OD")[0] == "doubao"
    assert (
        extract_share_url(
            "看这个 https://www.doubao.com/video-sharing?share_id=1&video_id=v2"
        )[0]
        == "doubao"
    )
    with pytest.raises(ParseError) as exc:
        extract_share_url("https://www.youtube.com/watch?v=1")
    assert exc.value.code == UNSUPPORTED_PLATFORM.code


def test_video_sharing_url_and_ids():
    url = (
        "https://www.doubao.com/video-sharing?source_type=mobile"
        "&share_id=55001037844729858&video_id=v0369cg10004d9sa0r27dldf56q8g3j0"
    )
    assert is_video_sharing_url(url) is True
    assert is_video_sharing_url("https://www.doubao.com/thread/xa1tW3szrrCdLo9OD") is False
    assert video_ids_from_url(url) == (
        "55001037844729858",
        "v0369cg10004d9sa0r27dldf56q8g3j0",
    )


def test_parse_thread_prefers_raw_image():
    work = parse_thread_data(THREAD_SHARE)
    assert work.type == "images"
    assert work.title == "果冻风动漫图生成"
    assert work.author == "楉野"
    assert work.image_urls == [
        "https://p11.example/rc_gen_image/a.jpeg~tplv-image_raw.png"
    ]
    assert "image_raw" in work.image_groups[0][0]
    assert "i_dld_wm" in work.image_groups[0][1]


THREAD_HTML_SINGLE_QUOTE = (
    "<script data-fn-args='"
    + json.dumps(
        [
            "thread_(token)/page",
            [
                {
                    "key": "shareInfo",
                    "routerDataFnArgs": [json.dumps({"data": THREAD_SHARE})],
                }
            ],
        ]
    ).replace('"', "&quot;")
    + "'></script>"
)


def test_extract_thread_payload_from_html():
    payload = extract_thread_payload(THREAD_HTML)
    assert payload is not None
    assert payload["share_info"]["share_id"] == "xa1tW3szrrCdLo9OD"


def test_extract_thread_payload_supports_single_quote_variant():
    """分享页有一种变体用单引号包裹 data-fn-args，服务端会随机返回。

    只匹配双引号会漏掉该变体，曾导致 doubao-thread 反复 PARSE_FAILED。
    """
    payload = extract_thread_payload(THREAD_HTML_SINGLE_QUOTE)
    assert payload is not None
    assert payload["share_info"]["share_id"] == "xa1tW3szrrCdLo9OD"


def test_parse_video_payload():
    work = parse_video_payload(VIDEO_PAYLOAD)
    assert work.type == "video"
    assert work.author == "甜甜"
    assert work.title.startswith("【精准提示词】")
    assert work.video_url.endswith("play.mp4?lr=video_gen_watermark_dyn")
    assert work.video_fallbacks[0].startswith("https://v5.example/")
    assert work.cover_url.endswith("cover.jpg")


def test_parse_api_routes_doubao(monkeypatch):
    import app.main as m
    from app.parsers.kuaishou import ParsedWork

    called = {"platform": ""}

    async def fake_doubao(url, timeout=15.0, client=None):
        called["platform"] = "doubao"
        return ParsedWork(
            type="video",
            title="豆包标题",
            author="豆包作者",
            video_url="https://cdn.example/doubao.mp4",
        )

    monkeypatch.setattr(m, "parse_doubao", fake_doubao)
    r = client.post(
        "/api/parse",
        json={
            "input": "https://www.doubao.com/video-sharing?share_id=1&video_id=v9"
        },
    )
    body = r.json()
    assert called["platform"] == "doubao"
    assert body["ok"] is True
    assert body["type"] == "video"
    assert body["title"] == "豆包标题"
    assert body["variants"][0]["label"] == "默认"
