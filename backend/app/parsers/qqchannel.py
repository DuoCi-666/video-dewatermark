"""腾讯频道（pd.qq.com）解析。

分享页是 Nuxt 3 SSR：帖子正文、作者、媒体地址都在服务端渲染好的
`<script id="__NUXT_DATA__">` 里（devalue 扁平化数组，数字是下标引用）。

两个坑：

1. **站点前置了 EdgeOne Bot 防护，拦截依据是 TLS 握手指纹（JA3）** —— 同样的
   UA / 出口 IP，httpx 3/3 被拦成挑战页，curl_cffi 3/3 正常拿到页面。所以这里
   不复用共享的 httpx 客户端，而是按请求用 curl_cffi 走真实浏览器指纹。
2. **页面里混着 "频道推荐流"**（`pinia.feedStore.shareLandHotFeeds`），也带 mp4。
   如果只按正则捞 mp4 会抓到别人的视频。分享的那条帖子在
   `pinia.feedStore.feedDetail`，只从这里取。

媒体地址带短时效签名（`dis_k` / `dis_t`），因此平台在注册表里标了
`signed_media=True`，媒体代理遇到全部候选失败时会重新解析刷新地址。
"""

from __future__ import annotations

import asyncio
import json
import re
from html import unescape

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

# 实测 safari_ios 与 chrome 都能过防护；先试轻量的移动端指纹，失败再退桌面端
IMPERSONATE_TARGETS: tuple[str, ...] = ("safari_ios", "chrome")

NUXT_DATA_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)

CHALLENGE_HINTS = ("eo-bot-js-token", "eo-bot-token")
DELETED_HINTS = ("已被删除", "已删除", "内容不存在", "帖子不存在", "作品已失效", "作品不存在")

# devalue 的包装类型：值是 [标记, 下标]
WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "Raw"}


class ChallengeBlocked(Exception):
    """所有浏览器指纹都被 EdgeOne 挑战页拦下。"""


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def resolve_devalue(pool: list) -> object:
    """还原 Nuxt 的 devalue 扁平数组：整数是下标引用，列表可带包装标记。"""
    memo: dict[int, object] = {}

    def follow(value: object) -> object:
        # bool 是 int 的子类，必须先判掉，否则 True 会被当下标
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return resolve(value)
        return value

    def resolve(index: int) -> object:
        if not isinstance(index, int) or index < 0 or index >= len(pool):
            return None
        if index in memo:
            return memo[index]
        node = pool[index]
        if isinstance(node, dict):
            out: dict = {}
            memo[index] = out
            for key, value in node.items():
                out[key] = follow(value)
            return out
        if isinstance(node, list):
            tag = node[0] if node and isinstance(node[0], str) else None
            if tag in WRAPPERS and len(node) == 2:
                result = follow(node[1])
                memo[index] = result
                return result
            if tag in ("null", "undefined"):
                memo[index] = None
                return None
            out_list: list = []
            memo[index] = out_list
            for value in node:
                out_list.append(follow(value))
            return out_list
        return node

    return resolve(0)


def _page_title(html: str) -> str:
    match = TITLE_RE.search(html)
    return _collapse(unescape(match.group(1))) if match else ""


def _looks_challenge(html: str) -> bool:
    lowered = html[:20000].lower()
    return any(hint in lowered for hint in CHALLENGE_HINTS)


def _looks_deleted(text: str) -> bool:
    return any(hint in text for hint in DELETED_HINTS)


def _feed_store(html: str) -> tuple[dict | None, dict | None]:
    """取出 pinia.feedStore，以及其中的那条分享帖 feedDetail。"""
    match = NUXT_DATA_RE.search(html)
    if not match:
        return None, None
    try:
        pool = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None, None
    if not isinstance(pool, list):
        return None, None
    try:
        root = resolve_devalue(pool)
    except (ValueError, TypeError, IndexError, RecursionError):
        return None, None
    if not isinstance(root, dict):
        return None, None
    pinia = root.get("pinia")
    store = pinia.get("feedStore") if isinstance(pinia, dict) else None
    if not isinstance(store, dict):
        return None, None
    feed = store.get("feedDetail")
    return (feed if isinstance(feed, dict) else None), store


def _pic_urls(item: object) -> list[str]:
    """一张图可能有多个尺寸/多 CDN 候选：picUrl 是原图，vecImageUrl 是各档缩略。"""
    if isinstance(item, str):
        return [item] if item.startswith("http") else []
    if not isinstance(item, dict):
        return []
    urls: list[str] = []
    for key in ("picUrl", "url", "coverUrl", "defaultUrl"):
        value = item.get(key)
        if isinstance(value, str) and value.startswith("http"):
            urls.append(value)
    variants = [entry for entry in (item.get("vecImageUrl") or []) if isinstance(entry, dict)]
    variants.sort(key=lambda entry: entry.get("width") or 0, reverse=True)
    for entry in variants:
        value = entry.get("url")
        if isinstance(value, str) and value.startswith("http"):
            urls.append(value)
    return list(dict.fromkeys(urls))


def video_candidates(video: dict) -> list[str]:
    """同一个视频的多档地址。实测腾讯频道的各档 playUrl 相同，去重后通常只剩一条。"""
    urls: list[str] = []
    primary = video.get("playUrl")
    if isinstance(primary, str) and primary:
        urls.append(primary)
    for entry in video.get("vecVideoUrl") or []:
        if isinstance(entry, dict):
            value = entry.get("playUrl")
            if isinstance(value, str) and value:
                urls.append(value)
    return list(dict.fromkeys(urls))


def image_candidates(feed: dict) -> list[list[str]]:
    """图文帖的图片（每张一组候选）。

    实测图文帖的图挂在顶层 `images`，`contents.images` / `share.images` 都是空的，
    所以三处都要看（顶层优先）。
    """
    containers = [feed.get("images")]
    for key in ("contents", "share"):
        container = feed.get(key)
        if isinstance(container, dict):
            containers.append(container.get("images"))
    for items in containers:
        if not isinstance(items, list):
            continue
        groups = [urls for urls in (_pic_urls(item) for item in items) if urls]
        if groups:
            return groups
    return []


def _container_text(container: object) -> str:
    """从富文本容器取纯文本（`title` 与 `contents` 同构：contents[].text_content.text）。"""
    if not isinstance(container, dict):
        return ""
    parts: list[str] = []
    items = container.get("contents")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                inner = item.get("text_content")
                text = inner.get("text") if isinstance(inner, dict) else ""
                parts.append(str(text or item.get("text") or item.get("content") or ""))
    text = _collapse(" ".join(parts))
    if text:
        return text
    return _collapse(container.get("source_markdown"))


def _content_text(feed: dict) -> str:
    """正文优先，其次标题 —— 视频帖的帖文实测放在 `title` 里（`contents` 是空的）。"""
    for key in ("contents", "title"):
        text = _container_text(feed.get(key))
        if text:
            return text
    return ""


def _author_of(feed: dict) -> str:
    poster = feed.get("poster")
    if isinstance(poster, dict):
        nick = _collapse(poster.get("nick"))
        if nick:
            return nick
    return "腾讯频道"


def _channel_of(feed: dict) -> str:
    info = feed.get("channelInfo")
    if isinstance(info, dict):
        name = _collapse(info.get("guild_name"))
        if name:
            return name
    return ""


def _cover_of(feed: dict, videos: list[dict], images: list[list[str]]) -> str | None:
    for video in videos:
        cover = video.get("cover")
        urls = _pic_urls(cover)
        if urls:
            return urls[0]
    direct = _pic_urls(feed.get("cover"))
    if direct:
        return direct[0]
    if images:
        return images[0][0]
    return None


def _from_ldjson(html: str) -> ParsedWork:
    """兜底：页面头部的 schema.org VideoObject 结构化数据。"""
    for match in LDJSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        content_url = video.get("contentUrl")
        if not (isinstance(content_url, str) and content_url.startswith("http")):
            continue
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        title = _collapse(data.get("headline") or data.get("text")) or "腾讯频道视频"
        return ParsedWork(
            type="video",
            title=title,
            author=_collapse(author.get("name")) or "腾讯频道",
            cover_url=video.get("thumbnailUrl") if isinstance(video.get("thumbnailUrl"), str) else None,
            video_url=content_url,
        )
    raise PARSE_FAILED


def extract_work(html: str) -> ParsedWork:
    """纯函数：只吃 HTML，产出解析结果（便于离线快照测试）。"""
    feed, store = _feed_store(html)
    if feed is None:
        message = ""
        if isinstance(store, dict):
            raw = store.get("errorMessage")
            if isinstance(raw, str):
                message = raw
        page_title = _page_title(html)
        if _looks_deleted(message) or _looks_deleted(page_title):
            raise WORK_UNAVAILABLE
        return _from_ldjson(html)

    if feed.get("is_deleted") is True:
        raise WORK_UNAVAILABLE

    videos = [item for item in (feed.get("videos") or []) if isinstance(item, dict)]
    images = image_candidates(feed)
    author = _author_of(feed)
    channel = _channel_of(feed)
    text = _content_text(feed)

    if videos:
        candidates = video_candidates(videos[0])
        if not candidates:
            raise PARSE_FAILED
        return ParsedWork(
            type="video",
            title=text or channel or "腾讯频道视频",
            author=author,
            cover_url=_cover_of(feed, videos, images),
            video_url=candidates[0],
            video_fallbacks=candidates[1:],
        )

    if images:
        return ParsedWork(
            type="images",
            title=text or channel or "腾讯频道图文",
            author=author,
            cover_url=images[0][0],
            image_urls=[group[0] for group in images],
            image_groups=images,
        )

    if text:
        # 纯文字帖：没有可下载媒体，按「不可用」处理，避免前端拿到空白卡片
        raise WORK_UNAVAILABLE
    raise PARSE_FAILED


def _fetch_sync(url: str, timeout: float) -> tuple[int, str]:
    """按请求用 curl_cffi 走真实浏览器指纹（httpx 会被 EdgeOne 拦）。"""
    from curl_cffi import requests as cffi_requests

    blocked = False
    last_status = 0
    last_html = ""
    for target in IMPERSONATE_TARGETS:
        try:
            session = cffi_requests.Session(impersonate=target)
        except (ValueError, TypeError):
            continue
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
        except Exception:  # noqa: BLE001 - 网络层异常统一按"换个指纹重试"处理
            continue
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        last_status = response.status_code
        last_html = response.text or ""
        if "__NUXT_DATA__" in last_html:
            return last_status, last_html
        if _looks_challenge(last_html):
            blocked = True
            continue
        return last_status, last_html
    if blocked:
        raise ChallengeBlocked()
    if not last_html:
        raise ChallengeBlocked()
    return last_status, last_html


async def parse(url: str, timeout: float = 15.0, client: object = None) -> ParsedWork:
    """腾讯频道解析入口。

    刻意忽略共享的 httpx `client`：该站按 TLS 指纹拦截，必须用 curl_cffi。
    curl_cffi 是同步库，放进线程池执行，避免阻塞事件循环。
    """
    try:
        status, html = await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync, url, timeout), timeout=timeout + 8.0
        )
    except asyncio.TimeoutError as exc:
        raise PARSE_TIMEOUT from exc
    except ChallengeBlocked as exc:
        raise RISK_CONTROL from exc

    if status in (404, 410):
        raise WORK_UNAVAILABLE
    if status >= 400 or not html:
        raise PARSE_FAILED
    if _looks_challenge(html):
        raise RISK_CONTROL
    return extract_work(html)
