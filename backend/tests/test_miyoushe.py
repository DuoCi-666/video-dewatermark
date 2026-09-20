from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ParseError
from app.main import app
from app.parsers import miyoushe
from app.parsers.extract import extract_share_url
from app.parsers.kuaishou import ParsedWork

client = TestClient(app)

VIDEO_480 = "https://prod-vod-sign.miyoushe.com/video-480.mp4?auth_key=old-480"
VIDEO_720 = "https://prod-vod-sign.miyoushe.com/video-720.mp4?auth_key=old-720"
VIDEO_1080 = "https://prod-vod-sign.miyoushe.com/video-1080.mp4?auth_key=old-1080"
VIDEO_2K = "https://prod-vod-sign.miyoushe.com/video-2k.mp4?auth_key=old-2k"
VIDEO_1080_BAK = "https://prod-vod-sign.miyoushe.com/video-1080-bak.mp4?auth_key=bak-1080"
COVER = "https://upload-bbs.miyoushe.com/upload/cover.png"
IMG1 = "https://upload-bbs.miyoushe.com/upload/one.jpg"
IMG2 = "https://upload-bbs.miyoushe.com/upload/two.jpg"


def video_payload() -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "post": {
                "post": {
                    "post_id": "78046513",
                    "subject": "看好了徒儿，为师只教一遍",
                    "cover": COVER,
                    "is_deleted": 0,
                    "deleted_at": 0,
                    "images": [COVER],
                },
                "user": {"nickname": "KristyQAQ"},
                "image_list": [{"url": COVER, "width": 2048, "height": 1536}],
                "vod_list": [
                    {
                        "id": "2097253788158005248",
                        "cover": COVER,
                        "resolutions": [
                            {"url": VIDEO_480, "definition": "480P", "label": "480P", "height": 480},
                            {"url": VIDEO_720, "definition": "720P", "label": "720P", "height": 720},
                            {"url": VIDEO_1080, "definition": "1080P", "label": "1080P", "height": 1080},
                            {"url": VIDEO_2K, "definition": "2K", "label": "2K", "height": 1440},
                        ],
                        "backup_resolutions": [
                            {"url": VIDEO_1080_BAK, "definition": "1080P", "label": "1080P", "height": 1080},
                        ],
                    }
                ],
            }
        },
    }


def images_payload() -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "post": {
                "post": {
                    "post_id": "23483079",
                    "subject": "瑶瑶cos|是可爱的像小团雀的瑶",
                    "cover": IMG1,
                    "is_deleted": 0,
                    "deleted_at": 0,
                    "images": [IMG1, IMG2],
                },
                "user": {"nickname": "方方的冰糖w"},
                "image_list": [{"url": IMG1}, {"url": IMG2}],
                "vod_list": [],
            }
        },
    }


def test_post_id_from_hash_and_path():
    assert miyoushe.post_id_from_url("https://m.miyoushe.com/ys?channel=vivo/#/article/78046513") == "78046513"
    assert miyoushe.post_id_from_url("https://www.miyoushe.com/ys/article/23483079") == "23483079"
    assert miyoushe.post_id_from_url("https://bbs.mihoyo.com/ys/article/1") == "1"
    assert miyoushe.post_id_from_url("https://www.miyoushe.com/ys") is None


def test_extract_share_url_miyoushe():
    key, url = extract_share_url(
        "米游社分享 https://m.miyoushe.com/ys?channel=vivo/#/article/78046513"
    )
    assert key == "miyoushe"
    assert url.startswith("https://m.miyoushe.com/")
    assert extract_share_url("https://www.miyoushe.com/ys/article/23483079")[0] == "miyoushe"
    assert extract_share_url("https://bbs.mihoyo.com/ys/article/1")[0] == "miyoushe"


def test_video_post_prefers_highest_quality_and_keeps_backup():
    work = miyoushe.extract_work(video_payload())
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.title == "看好了徒儿，为师只教一遍"
    assert work.author == "KristyQAQ"
    assert work.cover_url == COVER
    assert work.video_url == VIDEO_2K
    labels = [item.label for item in work.variants]
    assert labels == ["2K", "1080P", "720P", "480P"]
    assert work.variants[1].urls == [VIDEO_1080, VIDEO_1080_BAK]


def test_images_post_collects_originals():
    work = miyoushe.extract_work(images_payload())
    assert work.type == "images"
    assert work.author == "方方的冰糖w"
    assert work.image_urls == [IMG1, IMG2]
    assert work.image_groups == [[IMG1], [IMG2]]
    assert work.cover_url == IMG1


def test_video_post_does_not_fall_through_to_cover_image():
    work = miyoushe.extract_work(video_payload())
    assert work.type == "video"
    assert not work.image_urls


def test_deleted_retcode_maps_to_work_unavailable():
    with pytest.raises(ParseError) as info:
        miyoushe.extract_work({"retcode": 1001, "message": "帖子不存在"})
    assert info.value.code == "WORK_UNAVAILABLE"


def test_deleted_flag_maps_to_work_unavailable():
    payload = video_payload()
    payload["data"]["post"]["post"]["is_deleted"] = 1
    with pytest.raises(ParseError) as info:
        miyoushe.extract_work(payload)
    assert info.value.code == "WORK_UNAVAILABLE"


def test_empty_media_raises_parse_failed():
    payload = images_payload()
    payload["data"]["post"]["image_list"] = []
    payload["data"]["post"]["post"]["images"] = []
    payload["data"]["post"]["post"]["cover"] = ""
    with pytest.raises(ParseError) as info:
        miyoushe.extract_work(payload)
    assert info.value.code == "PARSE_FAILED"


def test_miyoushe_is_registered_as_signed_media():
    from app.parsers.extract import PLATFORMS, is_miyoushe_url, signed_media_platforms

    platform = next((item for item in PLATFORMS if item.key == "miyoushe"), None)
    assert platform is not None
    assert platform.name == "米游社"
    assert is_miyoushe_url("https://m.miyoushe.com/ys/#/article/1") is True
    assert is_miyoushe_url("https://miyoushe.com.evil.com/ys") is False
    assert "miyoushe" in signed_media_platforms()


def test_parse_api_routes_miyoushe(monkeypatch):
    import app.main as m

    called = {"url": ""}

    async def fake_parse(url, timeout=15.0, client=None):
        called["url"] = url
        return ParsedWork(
            type="video",
            title="米游社标题",
            author="作者",
            video_url=VIDEO_2K,
        )

    monkeypatch.setattr(m, "parse_miyoushe", fake_parse)
    response = client.post(
        "/api/parse",
        json={"input": "https://m.miyoushe.com/ys?channel=vivo/#/article/78046513"},
    )
    body = response.json()
    assert called["url"].endswith("78046513")
    assert body["ok"] is True
    assert body["type"] == "video"
    assert body["title"] == "米游社标题"
    assert body["variants"][0]["label"] == "默认"


def test_parse_happy_path_uses_post_api(monkeypatch):
    class FakeResponse:
        def json(self):
            return video_payload()

    class FakeClient:
        async def get(self, url, params=None, headers=None, follow_redirects=True, timeout=None):
            assert url == miyoushe.POST_API
            assert params["post_id"] == "78046513"
            return FakeResponse()

        async def aclose(self):
            return None

    work = asyncio.run(miyoushe.parse("https://www.miyoushe.com/ys/article/78046513", client=FakeClient()))
    assert work.type == "video"
    assert work.video_url == VIDEO_2K


def test_parse_retries_after_transport_error():
    """首次握手超时（代理抖动）不应直接判死：重试一次即可成功。"""
    calls = {"n": 0}

    class FlakyClient:
        async def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectTimeout("proxy handshake hung")
            return FakeJSONResponse(video_payload())

        async def aclose(self):
            return None

    work = asyncio.run(
        miyoushe.parse("https://www.miyoushe.com/ys/article/78046513", client=FlakyClient())
    )
    assert calls["n"] == 2
    assert work.type == "video"
    assert work.video_url == VIDEO_2K


def test_parse_all_timeouts_map_to_parse_timeout():
    """全部尝试都超时 → PARSE_TIMEOUT（而不是被当成解析失败）。"""
    calls = {"n": 0}

    class DeadClient:
        async def get(self, url, **kwargs):
            calls["n"] += 1
            raise httpx.ReadTimeout("upstream never answered")

        async def aclose(self):
            return None

    with pytest.raises(ParseError) as info:
        asyncio.run(
            miyoushe.parse("https://www.miyoushe.com/ys/article/78046513", client=DeadClient())
        )
    assert info.value.code == "PARSE_TIMEOUT"
    assert calls["n"] == miyoushe.MAX_ATTEMPTS


class FakeJSONResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_parse_without_post_id_fails():
    with pytest.raises(ParseError) as info:
        asyncio.run(miyoushe.parse("https://www.miyoushe.com/ys"))
    assert info.value.code == "PARSE_FAILED"


def test_refresh_info_enabled_for_miyoushe():
    from app.main import _refresh_info

    info = _refresh_info("miyoushe", "https://m.miyoushe.com/ys/#/article/1", "video")
    assert info == {
        "platform": "miyoushe",
        "share_url": "https://m.miyoushe.com/ys/#/article/1",
        "kind": "video",
        "index": 0,
    }


def test_miyoushe_proxy_env_uses_dedicated_proxied_client(monkeypatch):
    """配置 MIYOUSHE_PROXY 时：新建带代理的客户端，并忽略传入的共享 client。"""
    captured = {}

    class StubClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def get(self, *args, **kwargs):
            raise httpx.HTTPError("stub transport error")

        async def aclose(self):
            return None

    monkeypatch.setenv("MIYOUSHE_PROXY", "http://127.0.0.1:31280")
    monkeypatch.setattr(miyoushe.httpx, "AsyncClient", StubClient)

    with pytest.raises(ParseError):
        asyncio.run(
            miyoushe.parse(
                "https://m.miyoushe.com/ys?channel=vivo/#/article/23483079",
                timeout=1.0,
                client=object(),  # 共享 client 应被忽略
            )
        )

    assert captured.get("proxy") == "http://127.0.0.1:31280"
    assert captured.get("follow_redirects") is True


def test_miyoushe_without_proxy_reuses_shared_client(monkeypatch):
    """未配置 MIYOUSHE_PROXY 时：沿用传入的共享 client（行为不变）。"""
    monkeypatch.delenv("MIYOUSHE_PROXY", raising=False)
    monkeypatch.delenv("PARSE_PROXY", raising=False)
    used = {"count": 0}

    class SharedStub:
        async def get(self, *args, **kwargs):
            used["count"] += 1
            raise httpx.HTTPError("stub transport error")

        async def aclose(self):
            return None

    with pytest.raises(ParseError):
        asyncio.run(
            miyoushe.parse(
                "https://m.miyoushe.com/ys?channel=vivo/#/article/23483079",
                timeout=1.0,
                client=SharedStub(),
            )
        )

    assert used["count"] >= 1, "应使用传入的共享 client"
