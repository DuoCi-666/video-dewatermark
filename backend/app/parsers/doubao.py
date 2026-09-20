from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT
from app.parsers.kuaishou import ParsedWork

DOUBAO_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

VIDEO_SHARE_API = "https://www.doubao.com/creativity/share/get_video_share_info"
# 分享页有两种属性引号变体（服务端随机返回）：
#   大页面 data-fn-args="..."；小页面 data-fn-args='...'
# 只匹配双引号会漏掉小页面变体，导致 doubao-thread 随机解析失败。
FN_ARGS_RE = re.compile(r"""data-fn-args=(?:"([^"]*)"|'([^']*)')""")


def is_video_sharing_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = parse_qs(parsed.query)
    if "video-sharing" in path or "video_sharing" in path:
        return True
    return bool(query.get("video_id") or query.get("vid"))


def _headers(referer: str, accept: str = "text/html,application/xhtml+xml,*/*;q=0.8") -> dict[str, str]:
    return {
        "User-Agent": DOUBAO_UA,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.doubao.com",
        "Referer": referer,
    }


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def _title_from_prompt(prompt: str, fallback: str = "豆包视频") -> str:
    for line in str(prompt or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned if len(cleaned) <= 80 else cleaned[:80]
    return fallback


def _http_url(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    return None


def _image_variants(image: dict) -> list[str]:
    urls: list[str] = []
    for key in ("image_ori_raw", "image_ori", "image_preview", "image_thumb"):
        node = image.get(key)
        if isinstance(node, dict):
            url = _http_url(node.get("url"))
            if url:
                urls.append(url)
    return list(dict.fromkeys(urls))


def _asset_key(url: str) -> str:
    path = urlparse(url).path
    return path.split("~", 1)[0]


def _walk(node: object, visitor) -> None:
    if isinstance(node, dict):
        visitor(node)
        for value in node.values():
            _walk(value, visitor)
        return
    if isinstance(node, list):
        for item in node:
            _walk(item, visitor)
        return
    if isinstance(node, str):
        raw = node.strip()
        if raw[:1] in "{[":
            try:
                _walk(json.loads(raw), visitor)
            except json.JSONDecodeError:
                return


def _collect_images(data: dict) -> list[list[str]]:
    groups: list[list[str]] = []
    seen: set[str] = set()

    def visitor(node: dict) -> None:
        image = node.get("image") if isinstance(node.get("image"), dict) else None
        if image is None and any(k in node for k in ("image_ori_raw", "image_ori")):
            image = node
        if not isinstance(image, dict):
            return
        variants = _image_variants(image)
        if not variants:
            return
        key = _asset_key(variants[0])
        if key in seen:
            return
        if "rc_gen_image" not in key and "bot-chat-image" in key:
            return
        seen.add(key)
        groups.append(variants)

    _walk(data, visitor)
    return groups


def _find_share_data(node: object) -> dict | None:
    if isinstance(node, dict):
        inner = node.get("data")
        if isinstance(inner, dict) and "share_info" in inner:
            return inner
        if "share_info" in node:
            return node
        args = node.get("routerDataFnArgs")
        if isinstance(args, list) and args and isinstance(args[0], str):
            try:
                found = _find_share_data(json.loads(args[0]))
            except json.JSONDecodeError:
                found = None
            if found:
                return found
        for value in node.values():
            found = _find_share_data(value)
            if found:
                return found
        return None
    if isinstance(node, list):
        for item in node:
            found = _find_share_data(item)
            if found:
                return found
    if isinstance(node, str):
        raw = node.strip()
        if raw[:1] in "{[":
            try:
                return _find_share_data(json.loads(raw))
            except json.JSONDecodeError:
                return None
    return None


def extract_thread_payload(html: str) -> dict | None:
    for match in FN_ARGS_RE.finditer(html):
        raw = match.group(1) or match.group(2) or ""
        if not raw:
            continue
        text = unescape(raw)
        try:
            outer = json.loads(text)
        except json.JSONDecodeError:
            continue
        found = _find_share_data(outer)
        if found:
            return found
    return None


def parse_thread_data(data: dict) -> ParsedWork:
    share_info = data.get("share_info") or {}
    title = _collapse(share_info.get("share_name") or "") or "豆包图文"
    author = ((share_info.get("user") or {}).get("nick_name") or "豆包用户")
    groups = _collect_images(data)
    if groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=groups[0][0],
            image_urls=[group[0] for group in groups],
            image_groups=groups,
        )
    raise PARSE_FAILED


def parse_video_payload(payload: dict) -> ParsedWork:
    if payload.get("code") not in (0, None):
        raise PARSE_FAILED
    data = payload.get("data") or {}
    play = data.get("play_info") or {}
    main = _http_url(play.get("main"))
    backup = _http_url(play.get("backup"))
    if not main:
        raise PARSE_FAILED
    fallbacks = [u for u in (backup,) if u and u != main]
    author = ((data.get("user_info") or {}).get("nickname") or "豆包用户")
    title = _title_from_prompt(data.get("prompt") or "")
    return ParsedWork(
        type="video",
        title=title,
        author=author,
        cover_url=_http_url(play.get("poster_url")),
        video_url=main,
        video_fallbacks=fallbacks,
    )


def video_ids_from_url(url: str) -> tuple[str, str]:
    query = parse_qs(urlparse(url).query)
    share_id = (query.get("share_id") or [""])[0]
    vid = (query.get("video_id") or query.get("vid") or [""])[0]
    return share_id, vid


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
    try:
        try:
            if is_video_sharing_url(share_url):
                return await _parse_video(share_url, client)
            return await _parse_thread(share_url, client)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()


async def _parse_video(share_url: str, client: httpx.AsyncClient) -> ParsedWork:
    share_id, vid = video_ids_from_url(share_url)
    if not share_id or not vid:
        raise PARSE_FAILED
    headers = _headers(share_url, accept="application/json")
    response = await client.post(
        VIDEO_SHARE_API,
        json={"share_id": share_id, "vid": vid, "creation_id": ""},
        headers=headers,
        follow_redirects=True,
    )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise PARSE_FAILED from exc
    if not isinstance(payload, dict):
        raise PARSE_FAILED
    return parse_video_payload(payload)


async def _parse_thread(share_url: str, client: httpx.AsyncClient) -> ParsedWork:
    headers = _headers(share_url)
    response = await client.get(share_url, headers=headers, follow_redirects=True)
    payload = extract_thread_payload(response.text)
    if not payload:
        raise PARSE_FAILED
    return parse_thread_data(payload)
