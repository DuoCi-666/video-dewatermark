"""抖音 a_bogus 算法与原生详情兜底测试。

算法部分用参考实现（wujunwei928/parse-video）自带的确定性向量校验：
- SM3 两个标准向量；
- web 详情查询串；
- a_bogus 在固定入参下的确定输出（逐字节一致）。
"""
from __future__ import annotations

import asyncio

import httpx

from app.parsers import douyin, douyin_abogus

VIDEO_ID = "7450123456789012345"

WANT_QUERY = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1"
    "&version_code=290100&version_name=29.1.0&cookie_enabled=true"
    "&screen_width=1920&screen_height=1080&browser_language=zh-CN"
    "&browser_platform=Win32&browser_name=Chrome&browser_version=130.0.0.0"
    "&browser_online=true&engine_name=Blink&engine_version=130.0.0.0"
    "&os_name=Windows&os_version=10&cpu_core_num=12&device_memory=8&platform=PC"
    "&downlink=10&effective_type=4g&from_user_page=1&locate_query=false"
    "&need_time_list=1&pc_libra_divert=Windows&publish_video_strategy_type=2"
    "&round_trip_time=0&show_live_replay_strategy=1&time_list_query=0"
    f"&whale_cut_token=&update_version_code=170400&msToken=&aweme_id={VIDEO_ID}"
)

WANT_ABOGUS = (
    "E7mhBdugDifihdWk5l/LfY3q6fuVYmQ/0SVkMD2ffaDOJL39HMOk9exobQ4vpY2NZfmv2-ujy5kSYrrMicQnA3v6HSRKl2xp"
    "-g00t-P2so0j5ZhjCfuDnzfF-vzWt-Bd-Jd3Ech/ovKSKYi0AIee-wHvyhnFwo8sNiD4"
)


# ---- 算法向量 ----


def test_sm3_standard_vectors():
    assert douyin_abogus.sm3(b"abc").hex() == (
        "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )
    assert douyin_abogus.sm3(b"abcd" * 16).hex() == (
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
    )


def test_web_detail_query():
    assert douyin_abogus.web_detail_query(VIDEO_ID) == WANT_QUERY


def test_make_a_bogus_matches_reference_vector():
    got = douyin_abogus.make_a_bogus(
        WANT_QUERY, "GET", 1720000000123, 1720000000129, 1234, 5678, 9012
    )
    assert got == WANT_ABOGUS


def test_make_a_bogus_shape():
    got = douyin_abogus.make_a_bogus(
        douyin_abogus.web_detail_query(VIDEO_ID),
        "GET",
        1720000000123,
        1720000000129,
        1,
        2,
        3,
    )
    assert len(got) % 4 == 0
    assert set(got) <= set(douyin_abogus.ALPHABET + "=")


# ---- 原生详情兜底 ----


def _detail_body(desc: str = "native detail") -> dict:
    return {
        "aweme_detail": {
            "aweme_id": VIDEO_ID,
            "desc": desc,
            "author": {"nickname": "作者"},
            "video": {
                "play_addr": {"url_list": ["https://cdn/playwm/1.mp4"]},
                "cover": {"url_list": ["https://cdn/cover.jpg"]},
            },
            "images": [],
        }
    }


def test_fetch_detail_requires_cookie(monkeypatch):
    monkeypatch.delenv(douyin.COOKIE_ENV, raising=False)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_detail_body()))
        ) as c:
            return await douyin._fetch_detail(c, VIDEO_ID)

    assert asyncio.run(run()) is None


def test_fetch_detail_signs_request(monkeypatch):
    monkeypatch.setenv(douyin.COOKIE_ENV, "sessionid=test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["cookie"] = request.headers.get("cookie")
        captured["referer"] = request.headers.get("referer")
        return httpx.Response(200, json=_detail_body())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await douyin._fetch_detail(c, VIDEO_ID)

    detail = asyncio.run(run())
    assert detail is not None and detail["desc"] == "native detail"
    assert "a_bogus=" in captured["url"]
    assert f"aweme_id={VIDEO_ID}" in captured["url"]
    assert captured["cookie"] == "sessionid=test"
    assert captured["referer"] == "https://www.douyin.com/"


def test_fetch_detail_rejects_mismatched_id(monkeypatch):
    monkeypatch.setenv(douyin.COOKIE_ENV, "sessionid=test")
    body = _detail_body()
    body["aweme_detail"]["aweme_id"] = "999"

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ) as c:
            return await douyin._fetch_detail(c, VIDEO_ID)

    assert asyncio.run(run()) is None


def test_parse_falls_back_to_detail(monkeypatch):
    """分享页无内嵌数据 + 配置了 Cookie 时，应走详情接口兜底。"""
    monkeypatch.setenv(douyin.COOKIE_ENV, "sessionid=test")
    monkeypatch.setattr(douyin, "RETRY_INTERVAL", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if "aweme/detail" in str(request.url):
            return httpx.Response(200, json=_detail_body())
        return httpx.Response(200, text="<html><body>no router data</body></html>")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await douyin.parse(
                f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/", client=c
            )

    work = asyncio.run(run())
    assert work.type == "video"
    assert work.title == "native detail"
    # playwm → play
    assert work.video_url == "https://cdn/play/1.mp4"
