from __future__ import annotations

import asyncio
import json

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT
from app.parsers.kuaishou import ParsedWork

XHS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

MAX_ATTEMPTS = 3
RETRY_INTERVAL = 0.5


def _extract_initial_state(html: str) -> dict | None:
    """提取 window.__INITIAL_STATE__ = {...}（含 undefined 字面量，替换后解析）。"""
    i = html.find("window.__INITIAL_STATE__")
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    end = None
    for j in range(start, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        return None
    raw = html[start:end].replace("undefined", "null")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _pick_note(state: dict) -> dict | None:
    note_map = (state.get("note") or {}).get("noteDetailMap") or {}
    for wrap in note_map.values():
        if isinstance(wrap, dict):
            note = wrap.get("note") or {}
            if note.get("noteId"):
                return note
    # 新版移动端 SSR：笔记数据在 noteData.data.noteData（noteDetailMap 已废弃）
    note = ((state.get("noteData") or {}).get("data") or {}).get("noteData")
    if isinstance(note, dict) and note.get("noteId"):
        return note
    return None


def _image_groups(note: dict) -> list[list[str]]:
    """每张图组：urlDefault 优先，infoList 全尺寸作为备选 CDN。"""
    groups: list[list[str]] = []
    for img in note.get("imageList") or []:
        if not isinstance(img, dict):
            continue
        variants: list[str] = []
        default = img.get("urlDefault")
        if isinstance(default, str) and default:
            variants.append(default)
        for info in img.get("infoList") or []:
            url = (info or {}).get("url")
            if isinstance(url, str) and url:
                variants.append(url)
        deduped = list(dict.fromkeys(variants))
        if deduped:
            groups.append(deduped)
    return groups


def _video_urls(note: dict) -> list[str]:
    """收集视频直链：h264 全部 masterUrl（多码率/线路做备选）。"""
    media = ((note.get("video") or {}).get("media") or {}).get("stream") or {}
    urls: list[str] = []
    for track in media.get("h264") or []:
        url = (track or {}).get("masterUrl")
        if isinstance(url, str) and url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _display_title(note: dict) -> str:
    title = " ".join(str(note.get("title") or "").split())
    if title:
        return title
    desc = " ".join(str(note.get("desc") or "").split())
    return desc[:30] if desc else "小红书笔记"


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    headers = {
        "User-Agent": XHS_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            note = None
            for attempt in range(MAX_ATTEMPTS):
                page = await client.get(
                    share_url,
                    follow_redirects=True,
                    headers=headers,
                )
                state = _extract_initial_state(page.text)
                note = _pick_note(state) if state else None
                if note:
                    break
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_INTERVAL)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if not note:
        raise PARSE_FAILED

    title = _display_title(note)
    author = (note.get("user") or {}).get("nickname") or "小红书用户"

    if (note.get("type") or "normal") == "video":
        urls = _video_urls(note)
        if urls:
            return ParsedWork(
                type="video",
                title=title,
                author=author,
                cover_url=_image_groups(note)[0][0] if _image_groups(note) else None,
                video_url=urls[0],
                video_fallbacks=urls[1:5],
            )

    groups = _image_groups(note)
    if groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=groups[0][0],
            image_urls=[g[0] for g in groups],
            image_groups=groups,
        )

    raise PARSE_FAILED
