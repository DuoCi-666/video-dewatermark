from __future__ import annotations

import re
from html import unescape
from typing import Any

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

# —— bid（base62）↔ mid（10 进制）转换 ——
# 微博分享链接的尾段（如 weibo.com/{uid}/{bid}）是 mid 的 base62 编码，
# m.weibo.cn/statuses/show 需要的是十进制 mid。转换规则：从右往左每 4 位一组，
# 每组 base62 解码，除首组外不足 7 位前面补 0。
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _b62_decode(token: str) -> int:
    value = 0
    for ch in token:
        value = value * 62 + _ALPHABET.index(ch)
    return value


def bid_to_mid(bid: str) -> str:
    """把分享链接尾段的 bid 转成十进制 mid。

    规则：从右往左每 4 位一组，每组 base62 解码，除首组外不足 7 位前面补 0。
    """
    token = bid
    # 从右往左切成最多 4 位的组
    cut_list = [
        token[i - 4 if i >= 4 else 0 : i] for i in range(len(token), 0, -4)
    ]
    cut_list.reverse()
    parts: list[str] = []
    for i, chunk in enumerate(cut_list):
        decimal = str(_b62_decode(chunk))
        if i > 0 and len(decimal) < 7:
            decimal = "0" * (7 - len(decimal)) + decimal
        parts.append(decimal)
    return "".join(parts)


# weibo.com/{uid}/{bid} 或 m.weibo.cn/status/{mid} 或 /detail/{mid}
_WEIBO_URL_RE = re.compile(
    r"weibo\.com/(?:\d+)/([A-Za-z0-9]{3,})|"
    r"m\.weibo\.cn/(?:status|detail|p)/(\d+)"
)

SHOW_API = "https://m.weibo.cn/statuses/show"
GENVISITOR_API = "https://passport.weibo.com/visitor/genvisitor"
INCARNATE_API = "https://passport.weibo.com/visitor/visitor"

RISK_HINTS = ("验证码", "安全验证", "uni-testbox", "risk")


def _mid_from_url(url: str) -> str | None:
    match = _WEIBO_URL_RE.search(url or "")
    if not match:
        return None
    bid_or_mid = match.group(1) or match.group(2)
    if match.group(2):
        return match.group(2)
    return bid_to_mid(bid_or_mid)


def _collapse(text: Any) -> str:
    value = unescape(str(text or ""))
    value = re.sub(r"<[^>]+>", " ", value)  # 去掉内联 HTML 标签
    return " ".join(value.split())


async def _fetch_visitor_cookie(client: httpx.AsyncClient) -> None:
    """通过 genvisitor → incarnate 生成访客 Cookie（SUB/SUBP），绕开登录验证。"""
    response = await client.post(
        GENVISITOR_API,
        data={"cb": "gen_callback", "fp": "{}", "t": "1"},
        headers={"Referer": "https://passport.weibo.com/"},
    )
    match = re.search(r'"tid"\s*:\s*"([^"]+)"', response.text)
    if not match:
        raise PARSE_FAILED
    tid = match.group(1)
    await client.get(
        INCARNATE_API,
        params={"from": "weibo", "a": "incarnate", "t": tid, "ua": "", "lang": "zh-cn"},
        headers={"Referer": "https://weibo.com/"},
    )
    # 访客 Cookie(SUB/SUBP) 落在 .weibo.com 域，而 statuses/show 在 m.weibo.cn 上，
    # 需要手动复制一份到 .weibo.cn 域，否则请求不带 Cookie 会被 403 拒绝。
    for cookie in client.cookies.jar:
        if cookie.name in ("SUB", "SUBP"):
            client.cookies.set(cookie.name, cookie.value, domain=".weibo.cn", path="/")


def _is_risk(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _author_of(data: dict) -> str:
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    return _collapse(user.get("screen_name")) or "微博用户"


def _video_variants(page_info: dict) -> list[VideoVariant]:
    """按清晰度档位组织视频候选（区间：720p > 高清 > 标清）。"""
    urls = page_info.get("urls") if isinstance(page_info.get("urls"), dict) else {}
    media_info = (
        page_info.get("media_info") if isinstance(page_info.get("media_info"), dict) else {}
    )
    # 顺序即清晰度优先级：第一档会成为默认下载源
    tiers: list[tuple[int, str, str]] = [
        (0, "720p", urls.get("mp4_720p_mp4")),
        (1, "高清", urls.get("mp4_hd_mp4") or media_info.get("stream_url_hd")),
        (2, "标清", urls.get("mp4_ld_mp4") or media_info.get("stream_url")),
    ]
    variants: list[VideoVariant] = []
    seen: set[str] = set()
    for rank, label, url in tiers:
        if not isinstance(url, str) or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        variants.append(VideoVariant(label=label, rank=rank, urls=[url]))
    return variants


def extract_work(payload: dict) -> ParsedWork:
    if not isinstance(payload, dict) or payload.get("ok") != 1:
        # 作品已删 / 不存在：errno = 20101「该微博不存在」
        errno = str(payload.get("errno") or payload.get("error_code") or "") if isinstance(payload, dict) else ""
        if errno in {"20101", "20103"} or "不存在" in str(payload.get("message") or ""):
            raise WORK_UNAVAILABLE
        raise PARSE_FAILED
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not data:
        raise PARSE_FAILED

    title = _collapse(data.get("text")) or "微博"
    author = _author_of(data)

    page_info = data.get("page_info")
    if isinstance(page_info, dict) and page_info.get("type") == "video":
        variants = _video_variants(page_info)
        if variants:
            page_pic = page_info.get("page_pic")
            cover = (
                page_pic.get("url")
                if isinstance(page_pic, dict) and isinstance(page_pic.get("url"), str)
                else None
            )
            urls = [variant.urls[0] for variant in variants]
            return ParsedWork(
                type="video",
                title=title,
                author=author,
                cover_url=cover,
                video_url=urls[0],
                video_fallbacks=urls[1:],
                variants=variants,
            )

    # 图文/图集：取每张图的原始大图（pics[k].large.url）
    pics = [p for p in (data.get("pics") or []) if isinstance(p, dict)]
    image_groups: list[list[str]] = []
    for p in pics:
        large = p.get("large")
        url = large.get("url") if isinstance(large, dict) else None
        if isinstance(url, str) and url.startswith("http"):
            image_groups.append([url])
    if image_groups:
        image_urls = [group[0] for group in image_groups]
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=image_urls[0],
            image_urls=image_urls,
            image_groups=image_groups,
        )

    raise PARSE_FAILED


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    mid = _mid_from_url(share_url)
    if not mid:
        raise PARSE_FAILED

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={
                "User-Agent": UA,
                "Accept": "application/json,text/plain,*/*;q=0.8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://m.weibo.cn/",
            },
            follow_redirects=True,
            timeout=timeout,
        )
    try:
        try:
            await _fetch_visitor_cookie(client)
        except httpx.HTTPError:
            # 访客 cookie 拿不到时继续试一次：偶尔某些网络下可直连读取
            pass
        # statuses/show 依赖移动 JSON 请求头；少了 Referer / X-Requested-With 会返回 302。
        # 传共享 client（main 的 PARSE_CLIENT，follow_redirects=False）时没有这些默认头，
        # 因此在请求上显式补上，避免加到全局 client 影响其它平台。
        response = await client.get(
            SHOW_API,
            params={"id": mid},
            headers={
                "Accept": "application/json,text/plain,*/*;q=0.8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://m.weibo.cn/",
            },
        )
    except httpx.TimeoutException as exc:
        raise PARSE_TIMEOUT from exc
    except httpx.HTTPError as exc:
        raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if response.status_code in {404, 410}:
        raise WORK_UNAVAILABLE
    if response.status_code >= 400:
        if _is_risk(response.text):
            raise RISK_CONTROL
        raise PARSE_FAILED

    try:
        payload = response.json()
    except ValueError:
        raise PARSE_FAILED from None

    # 作品已删 / 不存在：errno = 20101「该微博不存在」
    if isinstance(payload, dict) and payload.get("ok") == 0:
        errno = str(payload.get("errno") or payload.get("error_code") or "")
        if errno in {"20101", "20103"} or "不存在" in str(payload.get("message") or ""):
            raise WORK_UNAVAILABLE
        raise PARSE_FAILED

    return extract_work(payload)
