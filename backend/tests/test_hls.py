"""HLS(m3u8) 转封装与转存测试。

覆盖：
- 纯函数（m3u8 判定、文件名归一）；
- 媒体代理分流：m3u8 源交给 ffmpeg，普通 mp4 源保持直通；HLS_TRANSCODE=0 时关闭；
- 转存缓存：命中缓存走文件（支持 Range/拖动），未命中时预览走流式、下载等转存完成。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app import main


def _resp(url: str, content_type: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


class _Item:
    filename = "测试视频.mp4"
    source_urls: list[str] = []
    refresh = None


def _patch_token(monkeypatch):
    monkeypatch.setattr(main.tokens, "get", lambda token: _Item())


def _patch_open(monkeypatch, upstream: httpx.Response):
    class _Client:
        async def aclose(self):
            pass

    async def fake_open(token, range_header=None):
        return _Client(), upstream

    monkeypatch.setattr(main, "_open_media", fake_open)


# ---- 纯函数 ----


def test_looks_like_hls():
    assert main._looks_like_hls("https://x/a.m3u8", None) is True
    assert main._looks_like_hls("https://x/a.m3u8?pkey=1", None) is True
    assert main._looks_like_hls("https://x/play", "application/vnd.apple.mpegurl") is True
    assert main._looks_like_hls("https://x/a.mp4", "video/mp4") is False
    assert main._looks_like_hls("https://x/a.jpg", "image/jpeg") is False


def test_to_mp4_name():
    assert main._to_mp4_name("标题.mp4") == "标题.mp4"
    assert main._to_mp4_name("标题.m3u8") == "标题.mp4"
    assert main._to_mp4_name("无扩展") == "无扩展.mp4"
    assert main._to_mp4_name("") == "video.mp4"


# ---- 代理分流 ----


def test_proxy_routes_hls_to_ffmpeg(monkeypatch):
    # 显式声明 ffmpeg 可用：否则在没装 ffmpeg 的环境（如 CI）里 _proxy 会走普通
    # 转发，_hls_response 不被调用，本用例会以 KeyError 形式误报失败。
    monkeypatch.setattr(main, "FFMPEG_AVAILABLE", True)
    _patch_token(monkeypatch)
    _patch_open(monkeypatch, _resp("https://cdn.x/play.m3u8", "application/vnd.apple.mpegurl"))

    called: dict = {}

    async def fake_response(token, item, url, as_attachment):
        called["token"] = token
        called["url"] = url
        called["attachment"] = as_attachment
        return main.StreamingResponse(iter([b"fmp4"]), media_type="video/mp4")

    monkeypatch.setattr(main, "_hls_response", fake_response)

    resp = asyncio.run(main._proxy("tok", as_attachment=False))
    assert called["token"] == "tok"
    assert called["url"] == "https://cdn.x/play.m3u8"
    assert called["attachment"] is False
    assert resp.media_type == "video/mp4"


def test_proxy_passthrough_mp4(monkeypatch):
    _patch_token(monkeypatch)
    _patch_open(monkeypatch, _resp("https://cdn.x/a.mp4", "video/mp4"))

    async def boom(*args, **kwargs):  # 不应被调用
        raise AssertionError("mp4 源不应走 HLS 处理")

    monkeypatch.setattr(main, "_hls_response", boom)

    resp = asyncio.run(main._proxy("tok", as_attachment=False))
    assert isinstance(resp, main.StreamingResponse)
    assert resp.headers["content-type"] == "video/mp4"


def test_proxy_hls_disabled_by_env(monkeypatch):
    _patch_token(monkeypatch)
    _patch_open(monkeypatch, _resp("https://cdn.x/play.m3u8", "application/vnd.apple.mpegurl"))
    monkeypatch.setattr(main, "HLS_TRANSCODE", False)

    async def boom(*args, **kwargs):
        raise AssertionError("关闭后不应走 HLS 处理")

    monkeypatch.setattr(main, "_hls_response", boom)

    resp = asyncio.run(main._proxy("tok", as_attachment=False))
    assert "mpegurl" in resp.headers["content-type"]


# ---- 转存缓存 ----


def test_hls_response_uses_file_when_cached(monkeypatch, tmp_path):
    f = tmp_path / "t.mp4"
    f.write_bytes(b"x" * 10)
    monkeypatch.setattr(main, "_hls_cached", lambda token: str(f))

    resp = asyncio.run(main._hls_response("tok", _Item(), "https://x/a.m3u8", False))
    assert isinstance(resp, main.FileResponse)
    assert resp.media_type == "video/mp4"


def test_hls_response_download_waits_for_file(monkeypatch, tmp_path):
    f = tmp_path / "d.mp4"
    f.write_bytes(b"x" * 10)
    monkeypatch.setattr(main, "_hls_cached", lambda token: None)

    async def fake_task(token, url, referer):
        return str(f)

    monkeypatch.setattr(main, "_hls_file_task", fake_task)

    resp = asyncio.run(main._hls_response("tok", _Item(), "https://x/a.m3u8", True))
    assert isinstance(resp, main.FileResponse)


def test_hls_response_preview_streams_and_schedules(monkeypatch):
    monkeypatch.setattr(main, "_hls_cached", lambda token: None)
    monkeypatch.setattr(main, "_HLS_TASKS", {})
    scheduled: dict = {}

    def fake_task(token, url, referer):
        scheduled["token"] = token
        return object()

    monkeypatch.setattr(main, "_hls_file_task", fake_task)

    resp = asyncio.run(main._hls_response("tok", _Item(), "https://x/a.m3u8", False))
    assert isinstance(resp, main.StreamingResponse)
    assert scheduled["token"] == "tok"


@pytest.mark.skipif(not main.FFMPEG_AVAILABLE, reason="ffmpeg 不可用")
def test_ffmpeg_available():
    assert main.FFMPEG_AVAILABLE is True
