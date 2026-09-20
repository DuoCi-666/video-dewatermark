"""皮皮虾（h5.pipix.com）分享链接解析。

链路（2026-09 实测）
--------------------
1. 分享短链 ``https://h5.pipix.com/s/<token>/`` 是 302，``Location`` 指向
   ``https://h5.pipix.com/ppx/item/<item_id>?app_id=1319&app=super&...``。
2. item 页是 SSR + 前端 hydration：页面里有一枚
   ``<script id="RENDER_DATA" type="application/json">``，其内容是 **URL 编码** 的
   JSON（需 ``urllib.parse.unquote`` 后再 ``json.loads``），顶层含
   ``ppxItemDetail`` 节点，``ppxItemDetail.item`` 即作品详情。

内容类型
--------
- 视频：``item.video.video_download.url_list[]``（多 CDN 候选，元素形如
  ``{"url": ..., "expires": 604800}``）；封面取 ``item.video.cover_image.url_list``
  或 ``item.cover.url_list``。
- 图集：``item.note.multi_image[]``，每张图的 ``url_list[].url`` 是
  ``...noop-v4:...`` 的**无水印原图**；注意 ``download_list`` 里是 ``...logo...``
  （带站点水印），故图文一律取 ``url_list``。

标题
----
实测正文常见为无意义的 ``"cos"``，标题兜底链：``item.content`` → ``item.share.title``
→ 默认「皮皮虾作品」。

直链与防盗链
------------
CDN 域名 ``*.ppxvod.com``（视频）/ ``*.ppx-sign.byteimg.com``（图片）**不校验
Referer**，共享 client 直接可取；图片/视频均支持 Range（206）。
直链带短时效签名（视频 ``expires`` 秒级、图片 ``x-expires`` 绝对时间戳），
故平台注册为 ``signed_media=True``，失效后由媒体代理自动重解析换新地址。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

SHARE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

RENDER_DATA_RE = re.compile(
    r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', re.S
)
# 长分享链接里直接带 item_id
_ITEM_ID_RE = re.compile(r"/ppx/item/(\d+)")

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁", "security")

DEFAULT_TITLE = "皮皮虾作品"
DEFAULT_AUTHOR = "皮皮虾"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _is_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _item_id_from(url: str) -> str | None:
    """从长分享链接取 item_id（短链需先请求取 302 Location）。"""
    match = _ITEM_ID_RE.search(url or "")
    return match.group(1) if match else None


def _item_id_from_location(location: str) -> str | None:
    if not location:
        return None
    match = _ITEM_ID_RE.search(location)
    return match.group(1) if match else None


def _extract_render_data(html: str) -> dict | None:
    """取 RENDER_DATA（URL 编码的 JSON）并解码。"""
    match = RENDER_DATA_RE.search(html or "")
    if not match:
        return None
    raw = match.group(1)
    for candidate in (unquote(raw), raw):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _item_of(render: dict) -> dict | None:
    node = render.get("ppxItemDetail")
    if not isinstance(node, dict):
        return None
    item = node.get("item")
    return item if isinstance(item, dict) else None


def _collect_urls(node: Any) -> list[str]:
    """从图片/视频节点收集 url_list[].url（多 CDN 候选，按序去重）。"""
    out: list[str] = []
    if isinstance(node, dict):
        for entry in node.get("url_list") or []:
            url = entry.get("url") if isinstance(entry, dict) else entry
            if isinstance(url, str) and url.startswith("http") and url not in out:
                out.append(url)
    return out


def _author_name(item: dict) -> str:
    author = item.get("author")
    if isinstance(author, dict):
        name = _collapse(author.get("name"))
        if name:
            return name
    return DEFAULT_AUTHOR


def _title_of(item: dict) -> str:
    share = item.get("share") if isinstance(item.get("share"), dict) else {}
    for candidate in (item.get("content"), share.get("title")):
        value = _collapse(candidate)
        if value:
            return value
    return DEFAULT_TITLE


def _cover_of(item: dict) -> str | None:
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    cover_image = video.get("cover_image") if isinstance(video.get("cover_image"), dict) else {}
    urls = _collect_urls(cover_image)
    if urls:
        return urls[0]
    urls = _collect_urls(item.get("cover"))
    if urls:
        return urls[0]
    share = item.get("share") if isinstance(item.get("share"), dict) else {}
    large = share.get("large_image_url")
    if isinstance(large, str) and large.startswith("http"):
        return large
    return None


def _video_variants(item: dict) -> list[VideoVariant]:
    """视频档位。皮皮虾的 video_download 是默认档（多 CDN 候选）。

    另预留 video_high / video_mid / video_low（实测多为 null），有值才作为额外档位。
    """
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    variants: list[VideoVariant] = []
    seen: set[str] = set()

    def add(label: str, node: Any, rank: int) -> None:
        if not isinstance(node, dict):
            return
        urls = _collect_urls(node)
        urls = [u for u in urls if u not in seen]
        if not urls:
            return
        seen.update(urls)
        variants.append(VideoVariant(label=label, rank=rank, urls=urls))

    add("默认", video.get("video_download"), 0)
    add("高清", video.get("video_high"), 1)
    add("标清", video.get("video_mid"), 2)
    add("流畅", video.get("video_low"), 3)
    return variants


def extract_work(item: dict) -> ParsedWork:
    """把 ppxItemDetail.item 转成统一的 ParsedWork。"""
    if not isinstance(item, dict) or not item:
        raise WORK_UNAVAILABLE

    title = _title_of(item)
    author = _author_name(item)
    cover = _cover_of(item)

    # 图集：note.multi_image（取无水印原图 url_list）
    note = item.get("note") if isinstance(item.get("note"), dict) else {}
    multi_image = note.get("multi_image") or []
    image_groups: list[list[str]] = []
    for entry in multi_image:
        if isinstance(entry, dict):
            urls = _collect_urls(entry)
            if urls:
                image_groups.append(urls)
    if image_groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=cover,
            image_urls=[group[0] for group in image_groups],
            image_groups=image_groups,
        )

    # 视频
    variants = _video_variants(item)
    if not variants:
        # 有作品但无视频/图集媒体：转码中 / 非媒体类作品
        raise WORK_UNAVAILABLE

    return ParsedWork(
        type="video",
        title=title,
        author=author,
        cover_url=cover,
        video_url=variants[0].urls[0],
        video_fallbacks=variants[0].urls[1:],
        variants=variants,
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers=SHARE_HEADERS,
            follow_redirects=False,
            timeout=timeout,
        )
    try:
        item_id = _item_id_from(share_url)
        page_url = share_url
        if item_id is None:
            # 短链：请求一次拿 302 Location 里的 item_id
            try:
                response = await client.get(share_url, headers=SHARE_HEADERS)
            except httpx.TimeoutException as exc:
                raise PARSE_TIMEOUT from exc
            except httpx.HTTPError as exc:
                raise PARSE_FAILED from exc
            item_id = _item_id_from_location(response.headers.get("location") or "")
            if item_id is None:
                if _is_risk(response.text):
                    raise RISK_CONTROL
                raise PARSE_FAILED
            page_url = response.headers.get("location") or share_url

        # 拿分享页 HTML（若上面已请求过且是 200 页面，可复用；否则再取一次）
        try:
            page = await client.get(page_url, headers=SHARE_HEADERS)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if page.status_code in {404, 410}:
        raise WORK_UNAVAILABLE
    if page.status_code >= 400:
        if _is_risk(page.text):
            raise RISK_CONTROL
        raise PARSE_FAILED

    render = _extract_render_data(page.text)
    if render is None:
        if _is_risk(page.text):
            raise RISK_CONTROL
        raise PARSE_FAILED
    item = _item_of(render)
    if item is None:
        raise WORK_UNAVAILABLE
    return extract_work(item)
