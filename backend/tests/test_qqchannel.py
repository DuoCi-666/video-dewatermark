"""腾讯频道解析的测试。

夹具用**手写的最小 devalue 池**（和真实 __NUXT_DATA__ 同一套编码约定），
不引入上百 KB 的整页快照；真实站点的格式变化由 ops/healthcheck.py 每 30 分钟
打真实链接来兜底。

最关键的一条保障：页面里混着"频道推荐流"（也带 mp4），只能取 feedDetail 里的
视频，否则会下到别人的视频。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.errors import ParseError
from app.parsers import qqchannel
from app import tokens
from app.main import app
from app.parsers.kuaishou import ParsedWork

MAIN_VIDEO = "https://qchannelvideo.photo.qq.com/50154_main.f104002.mp4?dis_k=aaa&dis_t=1789223854"
MAIN_VIDEO_ALT = "https://qchannelvideo.photo.qq.com/50154_main.f104002.mp4?dis_k=bbb&dis_t=1789223855"
HOT_VIDEO = "https://qchannelvideo.photo.qq.com/50154_hotfeed.f104002.mp4?dis_k=ccc&dis_t=1789223999"
COVER = "https://channel.qpic.cn/psc?/channel/cover-origin"
COVER_SMALL = "https://channel.qpic.cn/psc?/channel/cover-200"


class Wrapped:
    """devalue 的包装值（Ref / Reactive ...），编码成 [标记, 下标]。"""

    def __init__(self, tag: str, value: object):
        self.tag = tag
        self.value = value


def encode_pool(root: object) -> list:
    """把嵌套结构编码成 devalue 扁平池：标量/容器各占一格，引用用下标表示。"""
    pool: list = []

    def put(node: object) -> int:
        index = len(pool)
        if isinstance(node, Wrapped):
            holder: list = []
            pool.append(holder)
            holder.append(node.tag)
            holder.append(put(node.value))
            return index
        if isinstance(node, dict):
            holder_dict: dict = {}
            pool.append(holder_dict)
            for key, value in node.items():
                holder_dict[key] = put(value)
            return index
        if isinstance(node, list):
            holder_list: list = []
            pool.append(holder_list)
            for value in node:
                holder_list.append(put(value))
            return index
        pool.append(node)
        return index

    put(root)
    return pool


def feed_payload() -> dict:
    """贴近真实结构的一份 feedDetail 载荷（推荐流在前，故意放干扰视频）。"""
    return {
        "data": {"nothing": "here"},
        "pinia": {
            "feedStore": {
                "shareLandHotFeeds": [
                    {"videos": [{"playUrl": HOT_VIDEO, "cover": {"picUrl": COVER}}]}
                ],
                "feedDetail": {
                    "id": "B_af8a8a6a4e3c0d001441152196116760660X60",
                    "is_deleted": None,
                    "createTime": "1787464367",
                    "poster": {"nick": ".夜诉泪", "uin": "None"},
                    "channelInfo": {"guild_name": "精彩美图分享屋", "name": "二次元"},
                    "contents": {"contents": ["今天的分享"], "images": [], "source_markdown": None},
                    "share": {"contentType": 3, "images": []},
                    "videos": [
                        {
                            "fileId": "50154_main",
                            "width": 720,
                            "height": 1280,
                            "playUrl": MAIN_VIDEO,
                            "vecVideoUrl": [
                                {"playUrl": MAIN_VIDEO, "levelType": 9},
                                {"playUrl": MAIN_VIDEO},
                                {"playUrl": MAIN_VIDEO_ALT},
                            ],
                            "cover": {
                                "picUrl": COVER,
                                "vecImageUrl": [{"url": COVER_SMALL, "width": 200, "levelType": 1}],
                            },
                        }
                    ],
                    "errorMessage": None,
                },
                "errorMessage": None,
            }
        },
        "state": {"$spost-is-share-land-page": Wrapped("Reactive", True)},
    }


def make_page(pool_or_html, title: str = "分享视频｜精彩美图分享屋｜腾讯频道", ldjson: dict | None = None) -> str:
    head = f"<title>{title}</title>"
    if ldjson is not None:
        head += f'<script type="application/ld+json">{json.dumps(ldjson, ensure_ascii=False)}</script>'
    if isinstance(pool_or_html, str):
        body = pool_or_html
    else:
        body = f'<script id="__NUXT_DATA__" type="application/json">{json.dumps(pool_or_html)}</script>'
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


def page_of(feed: dict) -> str:
    return make_page(encode_pool(feed))


# ---------- devalue 还原 ----------


def test_resolve_devalue_restores_nested_structure():
    pool = encode_pool({"a": {"b": [1, None, "x"]}, "c": Wrapped("ShallowRef", {"d": 2})})
    root = qqchannel.resolve_devalue(pool)
    assert root == {"a": {"b": [1, None, "x"]}, "c": {"d": 2}}


def test_resolve_devalue_is_robust_to_bad_index():
    # 下标越界 / 负数不应炸掉，只是取不到值
    assert qqchannel.resolve_devalue([{"a": 999, "b": -3}]) == {"a": None, "b": None}


def test_resolve_devalue_handles_null_marker():
    # devalue 用 ["null"] 这类标记表示空值
    assert qqchannel.resolve_devalue([{"x": 1}, ["null"]]) == {"x": None}


# ---------- 视频帖 ----------


def test_video_post_extracts_the_shared_one():
    work = qqchannel.extract_work(page_of(feed_payload()))
    assert isinstance(work, ParsedWork)
    assert work.type == "video"
    assert work.author == ".夜诉泪"
    assert work.video_url == MAIN_VIDEO
    assert work.cover_url == COVER
    assert work.title == "今天的分享"


def test_recommended_feed_video_is_never_used():
    """页面里的推荐流也带 mp4，且排在前面 —— 不能被当成分享视频。"""
    html = page_of(feed_payload())
    assert HOT_VIDEO in html  # 干扰项确实在页面里
    work = qqchannel.extract_work(html)
    every_url = [work.video_url, *work.video_fallbacks]
    assert HOT_VIDEO not in every_url
    assert all("hotfeed" not in url for url in every_url)


def test_video_candidates_are_deduped_and_keep_order():
    work = qqchannel.extract_work(page_of(feed_payload()))
    assert work.video_url == MAIN_VIDEO
    assert work.video_fallbacks == [MAIN_VIDEO_ALT]


def test_title_falls_back_to_channel_name_when_no_text():
    feed = feed_payload()
    feed["pinia"]["feedStore"]["feedDetail"]["contents"]["contents"] = []
    work = qqchannel.extract_work(page_of(feed))
    assert work.title == "精彩美图分享屋"


def test_title_field_is_used_when_contents_is_empty():
    """真实视频帖的帖文放在 `title` 里，`contents` 是空的（线上回归样本）。"""
    feed = feed_payload()
    detail = feed["pinia"]["feedStore"]["feedDetail"]
    detail["contents"]["contents"] = []
    detail["title"] = {"contents": [{"type": 1, "text_content": {"text": "分享视频"}}]}
    work = qqchannel.extract_work(page_of(feed))
    assert work.title == "分享视频"


def test_contents_text_wins_over_title():
    feed = feed_payload()
    detail = feed["pinia"]["feedStore"]["feedDetail"]
    detail["title"] = {"contents": [{"type": 1, "text_content": {"text": "标题"}}]}
    work = qqchannel.extract_work(page_of(feed))
    assert work.title == "今天的分享"


# ---------- 图文帖 ----------


def test_images_post_collects_groups():
    feed = feed_payload()
    detail = feed["pinia"]["feedStore"]["feedDetail"]
    detail["videos"] = []
    detail["contents"]["images"] = [
        {"picUrl": "https://channel.qpic.cn/psc?/img1", "vecImageUrl": [{"url": "https://channel.qpic.cn/psc?/img1-small", "width": 200}]},
        "https://channel.qpic.cn/psc?/img2",
    ]
    detail["cover"] = None
    work = qqchannel.extract_work(page_of(feed))
    assert work.type == "images"
    assert work.image_urls == ["https://channel.qpic.cn/psc?/img1", "https://channel.qpic.cn/psc?/img2"]
    assert work.image_groups[0] == ["https://channel.qpic.cn/psc?/img1", "https://channel.qpic.cn/psc?/img1-small"]
    assert work.cover_url == "https://channel.qpic.cn/psc?/img1"


def test_images_post_reads_top_level_images():
    """实测图文帖的图挂在顶层 `images`，`contents.images` 是空的。"""
    feed = feed_payload()
    detail = feed["pinia"]["feedStore"]["feedDetail"]
    detail["videos"] = []
    detail["contents"]["images"] = []
    detail["images"] = [{"picUrl": "https://channel.qpic.cn/psc?/top1", "width": 1323, "height": 920}]
    detail["cover"] = None
    work = qqchannel.extract_work(page_of(feed))
    assert work.type == "images"
    assert work.image_urls == ["https://channel.qpic.cn/psc?/top1"]
    assert work.cover_url == "https://channel.qpic.cn/psc?/top1"


def test_images_post_without_any_media_raises():
    feed = feed_payload()
    detail = feed["pinia"]["feedStore"]["feedDetail"]
    detail["videos"] = []
    detail["contents"]["contents"] = []
    with pytest.raises(ParseError) as info:
        qqchannel.extract_work(page_of(feed))
    assert info.value.code == "PARSE_FAILED"


# ---------- 失效 / 删除 ----------


def test_deleted_flag_maps_to_work_unavailable():
    feed = feed_payload()
    feed["pinia"]["feedStore"]["feedDetail"]["is_deleted"] = True
    with pytest.raises(ParseError) as info:
        qqchannel.extract_work(page_of(feed))
    assert info.value.code == "WORK_UNAVAILABLE"


def test_deleted_message_maps_to_work_unavailable():
    """帖子被删时 feedDetail 为 null，删除提示在 store.errorMessage 里。"""
    feed = feed_payload()
    feed["pinia"]["feedStore"]["feedDetail"] = None
    feed["pinia"]["feedStore"]["errorMessage"] = "该帖子已被删除"
    with pytest.raises(ParseError) as info:
        qqchannel.extract_work(page_of(feed))
    assert info.value.code == "WORK_UNAVAILABLE"


def test_deleted_page_title_maps_to_work_unavailable():
    html = make_page("<p>没有数据</p>", title="内容不存在｜腾讯频道")
    with pytest.raises(ParseError) as info:
        qqchannel.extract_work(html)
    assert info.value.code == "WORK_UNAVAILABLE"


# ---------- 兜底与异常 ----------


def test_ldjson_fallback_when_nuxt_data_missing():
    ldjson = {
        "@type": "DiscussionForumPosting",
        "headline": "结构化数据里的标题",
        "author": {"@type": "Person", "name": ".夜诉泪"},
        "video": {"@type": "VideoObject", "contentUrl": MAIN_VIDEO, "thumbnailUrl": COVER},
    }
    work = qqchannel.extract_work(make_page("<p>无 nuxt</p>", ldjson=ldjson))
    assert work.type == "video"
    assert work.video_url == MAIN_VIDEO
    assert work.author == ".夜诉泪"
    assert work.cover_url == COVER
    assert work.title == "结构化数据里的标题"


def test_garbage_page_raises_parse_failed():
    with pytest.raises(ParseError) as info:
        qqchannel.extract_work("<html><body>hello</body></html>")
    assert info.value.code == "PARSE_FAILED"


def test_challenge_page_is_detected():
    html = make_page('<script src="/x.js"></script><script>var EO-Bot-Js-Token="1";</script>')
    assert qqchannel._looks_challenge(html) is True


# ---------- parse() 的网络层分支（打桩，不联网） ----------


def _run(coro):
    return asyncio.run(coro)


def test_parse_maps_challenge_to_risk_control(monkeypatch):
    html = make_page('<script>EO-Bot-Js-Token</script>')

    def fake_fetch(url, timeout):
        return 200, html

    monkeypatch.setattr(qqchannel, "_fetch_sync", fake_fetch)
    with pytest.raises(ParseError) as info:
        _run(qqchannel.parse("https://pd.qq.com/s/abc"))
    assert info.value.code == "RISK_CONTROL"


def test_parse_maps_404_to_work_unavailable(monkeypatch):
    monkeypatch.setattr(qqchannel, "_fetch_sync", lambda url, timeout: (404, ""))
    with pytest.raises(ParseError) as info:
        _run(qqchannel.parse("https://pd.qq.com/s/gone"))
    assert info.value.code == "WORK_UNAVAILABLE"


def test_parse_maps_transport_exception_to_risk_control(monkeypatch):
    def boom(url, timeout):
        raise qqchannel.ChallengeBlocked()

    monkeypatch.setattr(qqchannel, "_fetch_sync", boom)
    with pytest.raises(ParseError) as info:
        _run(qqchannel.parse("https://pd.qq.com/s/abc"))
    assert info.value.code == "RISK_CONTROL"


def test_parse_happy_path_and_never_touches_shared_httpx_client(monkeypatch):
    html = page_of(feed_payload())

    class BoomClient:
        def __getattr__(self, name):  # pragma: no cover - 只在被误用时触发
            raise AssertionError("腾讯频道必须用 curl_cffi，不应触碰共享 httpx 客户端")

    monkeypatch.setattr(qqchannel, "_fetch_sync", lambda url, timeout: (200, html))
    work = _run(qqchannel.parse("https://pd.qq.com/s/d8fbql692?b=2", client=BoomClient()))
    assert work.video_url == MAIN_VIDEO


def test_fetch_sync_falls_back_to_second_fingerprint(monkeypatch):
    """第一个指纹吃到挑战页时，换下一个指纹重试。"""
    seen: list[str] = []
    challenge = make_page('<script>EO-Bot-Js-Token</script>')
    good = page_of(feed_payload())

    class FakeResponse:
        def __init__(self, text):
            self.status_code = 200
            self.text = text

    class FakeSession:
        def __init__(self, impersonate):
            seen.append(impersonate)

        def get(self, url, timeout, allow_redirects):
            return FakeResponse(challenge if len(seen) == 1 else good)

        def close(self):
            pass

    import curl_cffi.requests as cffi_requests

    monkeypatch.setattr(cffi_requests, "Session", FakeSession)
    status, html = qqchannel._fetch_sync("https://pd.qq.com/s/abc", 15.0)
    assert status == 200
    assert "__NUXT_DATA__" in html
    assert seen == list(qqchannel.IMPERSONATE_TARGETS[:2])


# ---------- 注册表 ----------


def test_qqchannel_is_registered_as_signed_media():
    from app.parsers.extract import PLATFORMS, is_qqchannel_url, signed_media_platforms

    platform = next((item for item in PLATFORMS if item.key == "qqchannel"), None)
    assert platform is not None, "腾讯频道未注册进 PLATFORMS"
    assert platform.name == "腾讯频道"
    assert is_qqchannel_url("https://pd.qq.com/s/d8fbql692?b=2") is True
    assert is_qqchannel_url("https://pd.qq.com.evil.com/s/x") is False
    assert "qqchannel" in signed_media_platforms()


def test_unsupported_message_lists_every_platform():
    from app.errors import ParseError, unsupported_platform
    from app.parsers.extract import PLATFORMS

    with pytest.raises(ParseError) as info:
        raise unsupported_platform()
    assert info.value.code == "UNSUPPORTED_PLATFORM"
    for item in PLATFORMS:
        assert item.name in info.value.message, f"提示语缺少 {item.name}"


def test_share_url_routing_picks_qqchannel():
    from app.parsers.extract import extract_share_url

    key, url = extract_share_url("点击链接查看腾讯频道帖子【分享视频】：https://pd.qq.com/s/d8fbql692?b=2")
    assert key == "qqchannel"
    assert url == "https://pd.qq.com/s/d8fbql692?b=2"


def test_media_host_does_not_require_referer():
    """实测腾讯频道 CDN 无 Referer 也能 206，代理层不应额外加 Referer。"""
    from app.main import _upstream_headers

    headers = _upstream_headers("https://qchannelvideo.photo.qq.com/50154_main.f104002.mp4")
    assert "Referer" not in headers
    # 拿不准的参数顺序也要保住：有 Range 时透传 Range
    ranged = _upstream_headers("https://qchannelvideo.photo.qq.com/x.mp4", "bytes=0-1")
    assert ranged["Range"] == "bytes=0-1"


# ---------- 短时效签名：刷新链路 ----------

client = TestClient(app)


def _fake_proxy_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_refresh_info_only_for_signed_media_platforms():
    from app.main import _refresh_info

    # 普通平台保持 None —— 行为完全不变
    assert _refresh_info("kuaishou", "https://v.kuaishou.com/x", "video") is None
    assert _refresh_info("bilibili", "https://b23.tv/x", "cover") is None
    info = _refresh_info("qqchannel", "https://pd.qq.com/s/x", "video")
    assert info == {
        "platform": "qqchannel",
        "share_url": "https://pd.qq.com/s/x",
        "kind": "video",
        "index": 0,
    }
    assert _refresh_info("qqchannel", "https://pd.qq.com/s/x", "image", 2)["index"] == 2


def test_urls_for_refresh_picks_by_kind_and_index():
    from app.main import _urls_for_refresh

    video = ParsedWork(type="video", title="t", author="a", video_url="v1", video_fallbacks=["v2"])
    assert _urls_for_refresh(video, {"kind": "video"}) == ["v1", "v2"]

    images = ParsedWork(
        type="images", title="t", author="a", image_groups=[["a1", "a2"], ["b1"]], image_urls=["a1", "b1"]
    )
    assert _urls_for_refresh(images, {"kind": "image", "index": 1}) == ["b1"]
    assert _urls_for_refresh(images, {"kind": "image", "index": 9}) == []

    with_cover = ParsedWork(type="video", title="t", author="a", cover_url="c1")
    assert _urls_for_refresh(with_cover, {"kind": "cover"}) == ["c1"]


def test_token_refresh_survives_restart_and_can_be_updated():
    refresh = {
        "platform": "qqchannel",
        "share_url": "https://pd.qq.com/s/d8fbql692?b=2",
        "kind": "video",
        "index": 0,
    }
    token = tokens.issue_multi(["https://qchannelvideo.photo.qq.com/old.mp4"], "video", "v.mp4", refresh=refresh)
    assert tokens.get(token).refresh == refresh

    tokens.reset()  # 模拟进程重启：从磁盘重新加载
    reloaded = tokens.get(token)
    assert reloaded is not None
    assert reloaded.refresh == refresh

    assert tokens.update_sources(token, ["https://qchannelvideo.photo.qq.com/new.mp4"]) is True
    assert tokens.get(token).source_urls == ["https://qchannelvideo.photo.qq.com/new.mp4"]
    assert tokens.update_sources("no-such-token", ["https://x/y.mp4"]) is False


def test_proxy_reparses_when_signed_media_urls_all_fail(monkeypatch):
    """签名过期（候选全 403）时应重新解析换新地址，且 token 原地更新。"""
    from app import main as main_module
    from app import tokens

    fresh = "https://qchannelvideo.photo.qq.com/50154_main.f104002.mp4?dis_k=new&dis_t=1789300000"
    seen_share_urls: list[str] = []

    async def fake_parse(share_url, timeout=15.0, client=None):
        seen_share_urls.append(share_url)
        return ParsedWork(type="video", title="t", author="a", video_url=fresh)

    monkeypatch.setattr(main_module, "parse_qqchannel", fake_parse)

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if "expired" in str(request.url):
            return httpx.Response(403, content=b"signature expired")
        return httpx.Response(
            206,
            content=b"0123456789",
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-9/64"},
        )

    monkeypatch.setattr("app.main.PROXY_CLIENT", _fake_proxy_client(handler))
    token = tokens.issue_multi(
        ["https://qchannelvideo.photo.qq.com/expired.mp4"],
        "video",
        "v.mp4",
        refresh={"platform": "qqchannel", "share_url": "https://pd.qq.com/s/d8fbql692?b=2", "kind": "video", "index": 0},
    )

    response = client.get(f"/api/media/{token}", headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.content == b"0123456789"
    assert seen_share_urls == ["https://pd.qq.com/s/d8fbql692?b=2"]
    assert tokens.get(token).source_urls == [fresh]


def test_proxy_does_not_reparse_for_unsigned_platforms(monkeypatch):
    """没有刷新描述的平台（K 家等）失败就报错，绝不触发重新解析。"""
    from app import main as main_module
    from app import tokens

    async def must_not_be_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("没有 refresh 描述时不应重新解析")

    monkeypatch.setattr(main_module, "parse_kuaishou", must_not_be_called)

    monkeypatch.setattr(
        "app.main.PROXY_CLIENT",
        _fake_proxy_client(lambda request: httpx.Response(403, content=b"denied")),
    )
    token = tokens.issue("https://cdn.example/dead.mp4", "video", "dead.mp4")
    response = client.get(f"/api/media/{token}")
    assert response.status_code == 404
    assert response.json()["code"] == "PARSE_FAILED"
