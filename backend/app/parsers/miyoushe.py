from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from app import proxies
from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

MIYOUSHE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
POST_API = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
ARTICLE_ID_RE = re.compile(r"(?:[#/]article/|post_id=|postId=)(\d+)", re.IGNORECASE)
MAX_ATTEMPTS = 3
RETRY_INTERVAL = 0.5
# 单次尝试的超时下限：总预算 15s 分给 3 次尝试约 5s/次；低于该值说明调用方
# 自己传了更紧的预算，仍保留一次可用的握手时间，避免重试还没来得及做就整段超时。
MIN_ATTEMPT_TIMEOUT = 4.0

QUALITY_RANK = {
    "8k": 0,
    "4k": 1,
    "2k": 2,
    "1440p": 2,
    "1080p": 3,
    "720p": 4,
    "480p": 5,
    "360p": 6,
}


def post_id_from_url(url: str) -> str | None:
    match = ARTICLE_ID_RE.search(url or "")
    return match.group(1) if match else None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": MIYOUSHE_UA,
        "Accept": "application/json,text/plain,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.miyoushe.com/",
        "Origin": "https://www.miyoushe.com",
    }


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _http_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, dict):
        return _http_url(value.get("url"))
    return None


def _deleted(message: str) -> bool:
    return any(token in message for token in ("不存在", "删除", "已删", "已下架"))


def _unwrap(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise PARSE_FAILED
    retcode = payload.get("retcode")
    if retcode not in (None, 0, "0"):
        message = _collapse(payload.get("message"))
        if _deleted(message):
            raise WORK_UNAVAILABLE
        raise PARSE_FAILED
    wrap = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    inner = wrap.get("post") if isinstance(wrap.get("post"), dict) else wrap
    if isinstance(inner.get("post"), dict):
        return inner
    if inner.get("post_id") or inner.get("subject"):
        return {"post": inner, "user": wrap.get("user") or {}, "image_list": wrap.get("image_list") or [], "vod_list": wrap.get("vod_list") or []}
    raise PARSE_FAILED


def _author_of(wrap: dict) -> str:
    user = wrap.get("user") if isinstance(wrap.get("user"), dict) else {}
    return _collapse(user.get("nickname")) or "米游社用户"


def _cover_of(wrap: dict, post: dict) -> str | None:
    for candidate in (wrap.get("cover"), post.get("cover")):
        url = _http_url(candidate)
        if url:
            return url
    return None


def _image_groups(wrap: dict, post: dict) -> list[list[str]]:
    groups: list[list[str]] = []
    seen: set[str] = set()
    for img in wrap.get("image_list") or post.get("images") or []:
        url = _http_url(img)
        if not url or url in seen:
            continue
        seen.add(url)
        groups.append([url])
    return groups


def _variant_rank(item: dict) -> tuple[int, int]:
    label = str(item.get("definition") or item.get("label") or "").lower()
    height = item.get("height")
    height_rank = -int(height) if isinstance(height, int) else 0
    for key, rank in QUALITY_RANK.items():
        if key in label:
            return (rank, height_rank)
    return (50, height_rank)


def _variant_label(item: dict) -> str:
    label = _collapse(item.get("label") or item.get("definition"))
    return label.upper() if label else "默认"


def _video_variants(vod: dict) -> list[VideoVariant]:
    buckets: dict[str, VideoVariant] = {}
    order: list[str] = []
    rows = list(vod.get("resolutions") or []) + list(vod.get("backup_resolutions") or [])
    ranked = sorted(
        [row for row in rows if isinstance(row, dict) and _http_url(row)],
        key=_variant_rank,
    )
    for row in ranked:
        url = _http_url(row)
        if not url:
            continue
        label = _variant_label(row)
        current = buckets.get(label)
        if current is None:
            buckets[label] = VideoVariant(label=label, rank=len(order), urls=[url])
            order.append(label)
            continue
        if url not in current.urls:
            current.urls.append(url)
    return [buckets[label] for label in order]


def extract_work(payload: dict) -> ParsedWork:
    wrap = _unwrap(payload)
    post = wrap.get("post") if isinstance(wrap.get("post"), dict) else {}
    if post.get("is_deleted") in (True, 1, "1") or post.get("deleted_at") not in (None, 0, "0"):
        raise WORK_UNAVAILABLE

    title = _collapse(post.get("subject")) or "米游社帖子"
    author = _author_of(wrap)
    cover = _cover_of(wrap, post)
    vod_list = [item for item in (wrap.get("vod_list") or []) if isinstance(item, dict)]

    if vod_list:
        variants = _video_variants(vod_list[0])
        if variants:
            urls = [url for variant in variants for url in variant.urls]
            return ParsedWork(
                type="video",
                title=title,
                author=author,
                cover_url=cover or _http_url(vod_list[0].get("cover")),
                video_url=urls[0],
                video_fallbacks=urls[1:],
                variants=variants,
            )

    groups = _image_groups(wrap, post)
    if groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=cover or groups[0][0],
            image_urls=[group[0] for group in groups],
            image_groups=groups,
        )

    raise PARSE_FAILED


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    post_id = post_id_from_url(share_url)
    if not post_id:
        raise PARSE_FAILED

    headers = _headers()
    # 米游社对海外 IP 断连（bbs-api.miyoushe.com TCP 建连即挂死 → PARSE_TIMEOUT）。
    # 配置 MIYOUSHE_PROXY 后改用专用代理客户端出网，不复用共享 PARSE_CLIENT；
    # 未配置时行为完全不变。海外部署说明见 README。
    proxy = proxies.proxy_for("miyoushe")
    attempt_timeout = max(MIN_ATTEMPT_TIMEOUT, timeout / MAX_ATTEMPTS)
    if proxy:
        client = httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=attempt_timeout, proxy=proxy
        )
        own_client = True
    else:
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(
                headers=headers, follow_redirects=True, timeout=attempt_timeout
            )

    payload = None
    last_error: httpx.HTTPError | None = None
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        for attempt in range(MAX_ATTEMPTS):
            remaining = deadline - loop.time()
            # 预算已耗尽就不再发起新尝试（首次尝试不受此限，保证窗口极小时仍发出一次）
            if attempt > 0 and remaining <= 0:
                break
            per_try = max(MIN_ATTEMPT_TIMEOUT, remaining / (MAX_ATTEMPTS - attempt))
            try:
                response = await client.get(
                    POST_API,
                    params={"post_id": post_id, "read": 1},
                    headers=headers,
                    follow_redirects=True,
                    timeout=per_try,
                )
            except httpx.TimeoutException as exc:
                # 超时 / 代理握手挂起：换一次重试，而不是让单次失败直接判死
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and payload.get("retcode") in (0, "0"):
                    break
                if isinstance(payload, dict):
                    # 有正常响应但业务码不对（作品状态 / 页面结构问题）：重试无意义，
                    # 保留最后一份 payload 交给 extract_work 归类（已删 / 解析失败）
                    last_error = None
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_INTERVAL)
    finally:
        if own_client:
            await client.aclose()

    if not isinstance(payload, dict):
        if isinstance(last_error, httpx.TimeoutException):
            raise PARSE_TIMEOUT from last_error
        if last_error is not None:
            raise PARSE_FAILED from last_error
        raise PARSE_FAILED
    return extract_work(payload)
