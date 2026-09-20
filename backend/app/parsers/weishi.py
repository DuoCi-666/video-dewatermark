"""腾讯微视（weishi.qq.com）分享链接解析。

平台辨识
--------
微视是腾讯旗下的短视频平台，分享域 ``video.weishi.qq.com``。
分享链为短链形如 ``https://video.weishi.qq.com/dpIibFVG``，经 302 跳到
H5 分享页 ``https://isee.weishi.qq.com/ws/app-pages/share/index.html``
（Vue SPA，页面无内嵌数据）。

链路（2026-09 实测）
--------------------
1. 短链 ``video.weishi.qq.com/<code>`` -> 302 ->
   ``isee.weishi.qq.com/ws/app-pages/share/index.html?id=<feedid>&spid=...``
2. 页面经 JS 调用 ``POST api.weishi.qq.com/trpc.weishi.weishi_h5_proxy.
   weishi_h5_proxy/WSH5GetPlayPage`` 取数据。

WSH5GetPlayPage 请求体::

    {"req_header":{},"req_body":{
        "feedid":"<feedid>",
        "recommendtype":0,
        "datalvl":"all",
        "_weishi_mapExt":{"traceid":"","need264":"1"}}}

响应：``rsp_body.feeds[0]``

媒体结构
--------
- **视频**：``video_spec_urls`` 为 dict，key 为适配档位（``"0"``/``"999"``/
  ``"45"``/``"46"`` 等），每个含 ``url``（直链）、``width``/``height``/
  ``videoQuality``/``videoCoding``/``fps``/``size``。不同 key 可能是
  同档位的不同 CDN 适配参数（wsadapt）或不同质量；``haveWatermark`` 为
  0 表示无水印。主字段 ``video_url`` 不可用（302），**必须用
  ``video_spec_urls``**。
- **封面**：``video_cover.static_cover.url``（https，``xp.qpic.cn``）。
- **作者**：``poster.nick``。
- **标题**：``feed_desc``。

防直链防盗链
-----------
视频 CDN（``v.weishi.qq.com``）有**反 Referer**行为：携带 Referer 返回
302（拒绝），不带 Referer 返回 206（正常）。因此本解析器标记
``signed_media=True``（媒体代理在续期时不带 Referer 取流）。

直链带短时效签名（``dis_t`` / ``weishi_play_expire``），过期后由媒体代理
重新解析换新地址。
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

API_URL = (
    "https://api.weishi.qq.com/trpc.weishi.weishi_h5_proxy.weishi_h5_proxy/WSH5GetPlayPage"
)
PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
API_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://isee.weishi.qq.com",
    "Referer": "https://isee.weishi.qq.com/",
}

# short link 302 -> final URL, feed id in ?id=<feedid>
_FEED_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9]+)")
_SHORT_LINK_HOST = "video.weishi.qq.com"
_SHARE_HOST = "isee.weishi.qq.com"

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁", "安全")
DEFAULT_TITLE = "微视作品"
DEFAULT_AUTHOR = "微视"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _is_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _to_https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def _feed_id_from_url(url: str) -> str | None:
    """Extract feed ID from any URL that carries ``id=<feedid>`` in query."""
    match = _FEED_ID_RE.search(url or "")
    return match.group(1) if match else None


def _variant_label(width: int, height: int, quality: int) -> str:
    q = {3: "高清", 2: "标准", 1: "流畅"}.get(quality, f"Q{quality}")
    return f"{q} {width}x{height}" if width and height else q


def _video_variants(specs: dict) -> list[VideoVariant]:
    """从 video_spec_urls 按 (height, videoQuality) 分档，每档保留多 CDN 候选。"""
    if not isinstance(specs, dict) or not specs:
        return []

    groups: dict[tuple[int, int, int], list[str]] = {}
    seen: set[str] = set()
    for key, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("haveWatermark") == 1:
            continue
        url = _to_https(spec.get("url") or "")
        if not url.startswith("https://") or url in seen:
            continue
        width = int(spec.get("width") or 0)
        height = int(spec.get("height") or 0)
        quality = int(spec.get("videoQuality") or 0)
        if width <= 0 or height <= 0:
            continue
        seen.add(url)
        gk = (height, quality, width)
        groups.setdefault(gk, []).append(url)

    if not groups:
        return []

    variants: list[VideoVariant] = []
    for (height, quality, width), urls in sorted(
        groups.items(), key=lambda kv: (-kv[0][0], -kv[0][1], -kv[0][2])
    ):
        label = _variant_label(width, height, quality)
        variants.append(VideoVariant(label=label, rank=len(variants), urls=urls))
    return variants


def _cover_url(feed: dict) -> str | None:
    cover = feed.get("video_cover")
    if not isinstance(cover, dict):
        return None
    sc = cover.get("static_cover")
    if not isinstance(sc, dict):
        return None
    url = _to_https(sc.get("url") or "")
    return url if url.startswith("https://") else None


def _author(feed: dict) -> str:
    poster = feed.get("poster")
    if isinstance(poster, dict):
        name = _collapse(poster.get("nick"))
        if name:
            return name
    return DEFAULT_AUTHOR


def _title(feed: dict) -> str:
    return _collapse(feed.get("feed_desc")) or DEFAULT_TITLE


def _video_url_from_feed(feed: dict) -> str | None:
    """直接从 video_url 字段取 URL（该字段 302 不可用，作为兜底日志/诊断用）。
    实际播放必须用 video_spec_urls。"""
    url = _to_https(feed.get("video_url") or "")
    return url if url.startswith("https://") else None


def extract_work(feed: dict) -> ParsedWork:
    if not isinstance(feed, dict) or not feed:
        raise PARSE_FAILED

    title = _title(feed)
    author = _author(feed)
    cover = _cover_url(feed)

    variants = _video_variants(feed.get("video_spec_urls"))
    if variants:
        return ParsedWork(
            type="video",
            title=title,
            author=author,
            cover_url=cover,
            video_url=variants[0].urls[0],
            video_fallbacks=variants[0].urls[1:],
            variants=variants,
        )

    raise WORK_UNAVAILABLE


async def _resolve_feed_id(share_url: str, client: httpx.AsyncClient) -> str:
    """从短链 302 跟踪拿到带 id= 的最终 URL，提取 feedid。"""
    try:
        resp = await client.get(share_url, headers=PAGE_HEADERS, follow_redirects=True)
    except httpx.TimeoutException as exc:
        raise PARSE_TIMEOUT from exc
    except httpx.HTTPError as exc:
        raise PARSE_FAILED from exc

    final_url = str(resp.url)
    feed_id = _feed_id_from_url(final_url)
    if feed_id:
        return feed_id

    # 302 可能没跟上（httpx 默认不跟跨域），从 Location header 取
    location = resp.headers.get("location", "")
    if location:
        feed_id = _feed_id_from_url(location)
        if feed_id:
            return feed_id

    # 从响应 body 里搜 id= 参数
    feed_id = _feed_id_from_url(resp.text[:4096])
    if feed_id:
        return feed_id

    raise PARSE_FAILED


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        feed_id = await _resolve_feed_id(share_url, client)

        # 调用 WSH5GetPlayPage API
        payload = {
            "req_header": {},
            "req_body": {
                "feedid": feed_id,
                "recommendtype": 0,
                "datalvl": "all",
                "_weishi_mapExt": {"traceid": "", "need264": "1"},
            },
        }
        try:
            resp = await client.post(API_URL, json=payload, headers=API_HEADERS)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc

        if resp.status_code >= 400:
            if _is_risk(resp.text):
                raise RISK_CONTROL
            raise PARSE_FAILED

        try:
            body = resp.json()
        except ValueError:
            if _is_risk(resp.text):
                raise RISK_CONTROL
            raise PARSE_FAILED from None

        rsp_body = body.get("rsp_body")
        if not isinstance(rsp_body, dict):
            raise PARSE_FAILED

        feeds = rsp_body.get("feeds")
        if not isinstance(feeds, list) or not feeds:
            err = _collapse(rsp_body.get("errmsg"))
            if "deleted" in err or "不存在" in err:
                raise WORK_UNAVAILABLE
            if _is_risk(err):
                raise RISK_CONTROL
            raise WORK_UNAVAILABLE

        feed = feeds[0]
        if not isinstance(feed, dict):
            raise PARSE_FAILED

        return extract_work(feed)
    finally:
        if own_client:
            await client.aclose()
