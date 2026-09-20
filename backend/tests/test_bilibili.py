import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.parsers.bilibili import (
    classify_bilibili,
    ids_from_url,
    parse_opus_state,
    parse_view_payload,
)
from app.parsers.extract import extract_share_url
from app.parsers.kuaishou import ParsedWork
from app import tokens

client = TestClient(app)

VIEW_PAYLOAD = {
    "code": 0,
    "data": {
        "bvid": "BV1LJGy6HEqA",
        "aid": 116645708238788,
        "cid": 38648810346,
        "title": "怎么不算夹摇",
        "pic": "http://i2.hdslb.com/bfs/archive/cover.jpg",
        "owner": {"name": "丸小杀"},
    },
}

PLAY_64 = {
    "quality": 64,
    "durl": [
        {
            "url": "https://upos-sz-estgoss.bilivideo.com/upgcxcode/a-64.mp4",
            "backup_url": [
                "https://upos-sz-mirroralib.bilivideo.com/upgcxcode/a-64.mp4",
            ],
        }
    ],
}

PLAY_16 = {
    "quality": 16,
    "durl": [
        {
            "url": "https://upos-sz-mirrorcoso1.bilivideo.com/upgcxcode/a-16.mp4",
            "backup_url": [],
        }
    ],
}

OPUS_STATE = {
    "opus": {
        "detail": {
            "modules": [
                {
                    "module_type": "MODULE_TYPE_TITLE",
                    "module_title": {"text": "【朵莉亚·王者荣耀】高清无水印皮肤壁纸，欢迎自取！"},
                },
                {
                    "module_type": "MODULE_TYPE_AUTHOR",
                    "module_author": {"name": "濯郎衣上尘"},
                },
                {
                    "module_type": "MODULE_TYPE_CONTENT",
                    "module_content": {
                        "paragraphs": [
                            {"para_type": 1, "text": {"nodes": []}, "pic": None},
                            {
                                "para_type": 2,
                                "pic": {
                                    "pics": [
                                        {
                                            "url": "http://i0.hdslb.com/bfs/article/one.png",
                                        }
                                    ]
                                },
                            },
                            {
                                "para_type": 2,
                                "pic": {
                                    "pics": [
                                        {
                                            "url": "https://i0.hdslb.com/bfs/article/two.png",
                                        }
                                    ]
                                },
                            },
                        ]
                    },
                },
            ]
        }
    }
}


def test_extract_share_url_bilibili():
    assert extract_share_url("【怎么不算夹摇-哔哩哔哩】 https://b23.tv/VsfMAOd") == (
        "bilibili",
        "https://b23.tv/VsfMAOd",
    )
    assert (
        extract_share_url("https://www.bilibili.com/video/BV1LJGy6HEqA")[0] == "bilibili"
    )
    assert extract_share_url("https://m.bilibili.com/opus/820473529637011462")[0] == (
        "bilibili"
    )


def test_classify_and_ids():
    assert classify_bilibili("https://b23.tv/VsfMAOd") == "short"
    assert classify_bilibili("https://www.bilibili.com/video/BV1LJGy6HEqA") == "video"
    assert classify_bilibili("https://m.bilibili.com/opus/820473529637011462") == "opus"
    bvid, aid, opus = ids_from_url(
        "https://www.bilibili.com/video/BV1LJGy6HEqA?p=1&share_from=ugc"
    )
    assert bvid == "BV1LJGy6HEqA"
    assert aid is None
    assert opus is None
    _, _, opus_id = ids_from_url("https://m.bilibili.com/opus/820473529637011462")
    assert opus_id == "820473529637011462"


def test_parse_view_payload_variants():
    work = parse_view_payload(VIEW_PAYLOAD, {64: PLAY_64, 16: PLAY_16})
    assert work.type == "video"
    assert work.title == "怎么不算夹摇"
    assert work.author == "丸小杀"
    assert work.cover_url.startswith("https://")
    assert [v.label for v in work.variants] == ["720P", "360P"]
    assert work.variants[0].urls[0].endswith("a-64.mp4")
    assert "mirroralib" in work.variants[0].urls[1]


def test_parse_opus_state_images():
    work = parse_opus_state(OPUS_STATE)
    assert work.type == "images"
    assert "朵莉亚" in work.title
    assert work.author == "濯郎衣上尘"
    assert len(work.image_urls) == 2
    assert all(u.startswith("https://") for u in work.image_urls)


def test_parse_api_routes_bilibili_video(monkeypatch):
    import app.main as m
    from app.parsers.kuaishou import VideoVariant

    called = {"platform": ""}

    async def fake_bili(url, timeout=15.0, client=None):
        called["platform"] = "bilibili"
        return ParsedWork(
            type="video",
            title="怎么不算夹摇",
            author="丸小杀",
            video_url="https://upos-sz-estgoss.bilivideo.com/a.mp4",
            variants=[
                VideoVariant(
                    label="720P",
                    rank=-64,
                    urls=["https://upos-sz-estgoss.bilivideo.com/a.mp4"],
                ),
                VideoVariant(
                    label="360P",
                    rank=-16,
                    urls=["https://upos-sz-mirrorcoso1.bilivideo.com/b.mp4"],
                ),
            ],
        )

    monkeypatch.setattr(m, "parse_bilibili", fake_bili)
    r = client.post(
        "/api/parse",
        json={"input": "【怎么不算夹摇-哔哩哔哩】 https://b23.tv/VsfMAOd"},
    )
    body = r.json()
    assert called["platform"] == "bilibili"
    assert body["ok"] is True
    assert body["type"] == "video"
    assert body["title"] == "怎么不算夹摇"
    assert body["variants"][0]["label"] == "720P"
    assert body["variants"][1]["label"] == "360P"


def test_parse_api_routes_bilibili_opus(monkeypatch):
    import app.main as m

    async def fake_bili(url, timeout=15.0, client=None):
        return ParsedWork(
            type="images",
            title="【朵莉亚·王者荣耀】高清无水印皮肤壁纸，欢迎自取！",
            author="濯郎衣上尘",
            image_urls=["https://i0.hdslb.com/bfs/article/one.png"],
            image_groups=[["https://i0.hdslb.com/bfs/article/one.png"]],
        )

    monkeypatch.setattr(m, "parse_bilibili", fake_bili)
    r = client.post(
        "/api/parse",
        json={"input": "【朵莉亚】 https://b23.tv/ARlrPu8"},
    )
    body = r.json()
    assert body["ok"] is True
    assert body["type"] == "images"
    assert len(body["images"]) == 1


def _fake_proxy_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_proxy_sends_bilibili_referer(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("referer", "")
        seen["range"] = request.headers.get("range", "")
        return httpx.Response(
            206,
            content=b"bili",
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-3/64"},
        )

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue(
        "https://upos-sz-estgoss.bilivideo.com/upgcxcode/a.mp4", "video", "a.mp4"
    )
    r = client.get(f"/api/media/{token}", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert seen["referer"] == "https://www.bilibili.com/"
    assert seen["range"] == "bytes=0-3"


def test_bili_proxy_env_uses_dedicated_proxied_client(monkeypatch):
    """配置 BILI_PROXY 时：新建带代理的客户端，且忽略传入的共享 client。"""
    import asyncio

    import pytest

    from app.parsers import bilibili as bili

    captured = {}

    class StubClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def get(self, *args, **kwargs):
            raise httpx.HTTPError("stub transport error")

        async def aclose(self):
            return None

    monkeypatch.setenv("BILI_PROXY", "http://127.0.0.1:31280")
    monkeypatch.setattr(bili.httpx, "AsyncClient", StubClient)

    shared_client = object()  # 不应被使用
    with pytest.raises(type(bili.PARSE_FAILED)):
        asyncio.run(
            bili.parse(
                "https://www.bilibili.com/video/BV1eqYx6UE9V",
                timeout=1.0,
                client=shared_client,
            )
        )

    assert captured.get("proxy") == "http://127.0.0.1:31280"
    assert captured.get("follow_redirects") is True


def test_bili_without_proxy_reuses_shared_client(monkeypatch):
    """未配置 BILI_PROXY 时：沿用传入的共享 client（行为不变）。"""
    import asyncio

    import pytest

    from app.parsers import bilibili as bili

    monkeypatch.delenv("BILI_PROXY", raising=False)
    used = {"count": 0}

    class SharedStub:
        async def get(self, *args, **kwargs):
            used["count"] += 1
            raise httpx.HTTPError("stub transport error")

        async def aclose(self):
            return None

    shared = SharedStub()
    with pytest.raises(type(bili.PARSE_FAILED)):
        asyncio.run(
            bili.parse("https://www.bilibili.com/video/BV1eqYx6UE9V", timeout=1.0, client=shared)
        )

    assert used["count"] >= 1, "应使用传入的共享 client"
