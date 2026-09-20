from __future__ import annotations

import asyncio
import json
import re

import httpx

import os
import random
import time

from app.errors import PARSE_FAILED, PARSE_TIMEOUT
from app.parsers import douyin_abogus
from app.parsers.kuaishou import USER_AGENT, ParsedWork

MAX_ATTEMPTS = 3
RETRY_INTERVAL = 0.6

# 原生详情兜底：分享页未内嵌作品数据时，改走抖音 web 详情接口（带 a_bogus 签名）。
# 需要部署者**自有的登录 Cookie**（敏感凭据，从环境变量读，不入库、不外传）；
# 未配置时该兜底不启用，行为与之前一致。
COOKIE_ENV = "DOUYIN_COOKIE"
DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
DETAIL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _extract_router_json(html: str) -> dict | None:
    """提取 window._ROUTER_DATA = {...} 的 JSON（大括号配对，容忍嵌套与转义）。"""
    i = html.find("window._ROUTER_DATA")
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
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
                try:
                    return json.loads(html[start : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _find_item(router: dict) -> dict | None:
    """遍历 loaderData 找携带 item_list 的页面数据（key 形如 video_(id)/page）。"""
    for pv in router.get("loaderData", {}).values():
        if not isinstance(pv, dict):
            continue
        for cv in pv.values():
            if isinstance(cv, dict):
                items = cv.get("item_list")
                if items:
                    return items[0]
    return None


def _no_watermark(url: str) -> str:
    """playwm(带水印) → play 接口并请求 1080p；源不支持时抖音自动回落。"""
    return url.replace("playwm", "play").replace("ratio=720p", "ratio=1080p")


def _last_url(entry: dict | None) -> str | None:
    if not isinstance(entry, dict):
        return None
    urls = [u for u in (entry.get("url_list") or []) if isinstance(u, str) and u]
    return urls[-1] if urls else None


def _video_id_from(url: str) -> str | None:
    match = re.search(r"/(?:video|note|slides)/(\d+)", url)
    return match.group(1) if match else None


async def _fetch_detail(client: httpx.AsyncClient, video_id: str) -> dict | None:
    """分享页无数据时的原生详情兜底（需配置 DOUYIN_COOKIE，否则返回 None）。"""
    cookie = (os.environ.get(COOKIE_ENV) or "").strip()
    if not cookie:
        return None

    query = douyin_abogus.web_detail_query(video_id)
    started = int(time.time() * 1000)
    finished = started + 4 + random.randint(0, 4)
    signature = douyin_abogus.make_a_bogus(
        query,
        "GET",
        started,
        finished,
        random.randint(0, 9999),
        random.randint(0, 9999),
        random.randint(0, 9999),
    )
    headers = {
        "User-Agent": DETAIL_UA,
        "Referer": "https://www.douyin.com/",
        "Cookie": cookie,
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
    }
    resp = await client.get(
        f"{DETAIL_API}?{query}&a_bogus={signature}",
        headers=headers,
        follow_redirects=True,
    )
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    detail = data.get("aweme_detail")
    if not isinstance(detail, dict):
        return None
    if str(detail.get("aweme_id")) != str(video_id):
        return None
    return detail


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            # PARSE_CLIENT 全局为 follow_redirects=False（快手手动手跳），抖音需要跟随，按请求开启
            # headers 按请求显式携带（共享连接池默认头是快手 UA，不能依赖）
            first = await client.get(
                share_url, follow_redirects=True, headers=headers
            )
            video_id = _video_id_from(str(first.url).split("?")[0])
            if not video_id:
                raise PARSE_FAILED
            # 分享页 SSR 数据注入是概率性的（实测约 80%），重试直到拿到 item_list
            item = None
            for attempt in range(MAX_ATTEMPTS):
                page = await client.get(
                    f"https://www.iesdouyin.com/share/video/{video_id}/",
                    follow_redirects=True,
                    headers=headers,
                )
                router = _extract_router_json(page.text)
                item = _find_item(router) if router else None
                if item:
                    break
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_INTERVAL)
            if item is None:
                # 分享页未内嵌数据：配置了 Cookie 时走原生详情兜底
                item = await _fetch_detail(client, video_id)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if not item:
        raise PARSE_FAILED

    caption = " ".join(str(item.get("desc") or "").split())
    author = (item.get("author") or {}).get("nickname") or "抖音用户"
    video = item.get("video") or {}
    cover = _last_url(video.get("cover")) or _last_url(video.get("origin_cover"))
    images = item.get("images") or []

    if images:
        groups: list[list[str]] = []
        for img in images:
            urls = [u for u in ((img or {}).get("url_list") or []) if isinstance(u, str)]
            if urls:
                groups.append([urls[-1]])
        if groups:
            return ParsedWork(
                type="images",
                title=caption or "抖音图文",
                author=author,
                cover_url=groups[0][0],
                image_urls=[g[0] for g in groups],
                image_groups=groups,
            )

    play_wm = _last_url(video.get("play_addr"))
    if play_wm:
        return ParsedWork(
            type="video",
            title=caption or "抖音视频",
            author=author,
            cover_url=cover,
            video_url=_no_watermark(play_wm),
        )

    raise PARSE_FAILED
