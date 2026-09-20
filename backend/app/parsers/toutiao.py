"""今日头条（toutiao.com）分享链接解析。

平台辨识
--------
今日头条 H5 分享域为 ``m.toutiao.com``（短链 ``m.toutiao.com/is/<code>/``），
PC 域 ``www.toutiao.com``，图文/视频详情页域名同为 ``toutiao.com``。
本解析器**只支持微头条图文**（``/w/<id>/``），短视频（``/video/<id>/``）
在网页端不提供真实视频流（详见下），会明确报「需在 App 内观看」。

链路（2026-09 实测）
--------------------
1. 分享链形如 ``https://m.toutiao.com/is/<code>/``，302 跳到具体页面：
   - 微头条图文 → ``https://m.toutiao.com/w/<id>/?...``
   - 短视频     → ``https://m.toutiao.com/video/<id>/?...``
2. 微头条图文页是**服务端渲染**，HTML 内嵌 ``<script id="RENDER_DATA"
   type="application/json">``，内容是 **URL 编码的 JSON**。解码后取
   ``articleInfo.thread.threadBase``：
   - 标题/文案：``title``（回退 ``content`` / ``richContent`` 纯文本）
   - 作者：``user.info.name``
   - 图片：``largeImageList[].url``（1280 宽大图）、``thumbImageList[]`` 为缩略图（不用）

   .. note::
      ``/w/`` 页面可能有 ``longImageShortShareUrl`` 等其它形态，统一走
      ``RENDER_DATA`` 即可。

不支持短视频的原因
------------------
``/video/<id>/`` 短视频页在网页端**不渲染真实视频**：页面主体是全站热门推荐
（与目标视频无关），并带有字节 ``byted_acrawler`` 反爬签名；实测（puppeteer
无头浏览器）点击「立即播放」也不会发起任何视频资源请求，页面只引导「打开 APP」。
这类回流视频（``from_aweme=1``）为 App 独占，网页端拿不到直链，故本解析器对
视频类型明确返回 ``WORK_UNAVAILABLE``（提示需在 App 内查看）。

媒体结构
--------
- 图片直链形如 ``https://p<n>-sign.toutiaoimg.com/tos-cn-i-<hash>/<id>~tplv-shrink:1280:<h>.jpeg?...&x-expires=<ts>&x-signature=<sig>``。
  去掉 ``~tplv-...`` 后缀会 403（签名与 URL 绑定），故直接使用带签名的
  ``largeImageList[].url``；该直链**带短时效签名**，注册 ``signed_media=True``。

防盗链
------
图片 CDN（``*.toutiaoimg.com``）实测**不校验 Referer**，直接可取。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

import httpx

from app.errors import (
    PARSE_FAILED,
    PARSE_TIMEOUT,
    RISK_CONTROL,
    WORK_UNAVAILABLE,
    ParseError,
)
from app.parsers.kuaishou import ParsedWork

# 短视频回流页在网页端不提供真实视频流：给用户更明确的提示（复用 422 状态码）
VIDEO_IN_APP_ONLY = ParseError(
    422, "WORK_UNAVAILABLE", "今日头条短视频仅支持在 App 内观看，暂无法解析"
)

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# /w/<id>  微头条图文；/video/<id> 短视频（不支持）
_W_ID_RE = re.compile(r"/(?:w|article|i)/a?(\d{6,})")
_VIDEO_ID_RE = re.compile(r"/video/a?(\d{6,})")
_ANY_ID_RE = re.compile(r"/(?:w|video|article|i)/a?(\d{6,})")

# RENDER_DATA 脚本
_RENDER_RE = re.compile(
    r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>', re.S
)

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁", "安全")

DEFAULT_TITLE = "今日头条作品"
DEFAULT_AUTHOR = "今日头条"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _strip_html(text: str) -> str:
    """把 richContent 里的标签去掉，保留可读文字。"""
    text = re.sub(r"<i class=\"emoji[\s\S]*?</i>", "", text or "")
    text = re.sub(r"<span class=\"emoji-name\">\[.*?\]</span>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return _collapse(text)


def _is_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _is_video_url(url: str) -> bool:
    return "/video/" in (url or "")


def _path_id(url: str) -> str | None:
    match = _ANY_ID_RE.search(url or "")
    return match.group(1) if match else None


def _decode_render_data(html: str) -> dict | None:
    match = _RENDER_RE.search(html or "")
    if not match:
        return None
    raw = match.group(1).strip()
    for candidate in (unquote(raw), raw):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _image_urls(thread_base: dict) -> list[str]:
    """取大图直链（去重保序），回退到 urlList。"""
    out: list[str] = []
    large = thread_base.get("largeImageList")
    if not isinstance(large, list):
        large = []
    for image in large:
        if not isinstance(image, dict):
            continue
        url = image.get("url")
        if not (isinstance(url, str) and url.startswith("http")):
            url_list = image.get("urlList")
            url = None
            if isinstance(url_list, list) and url_list and isinstance(url_list[0], dict):
                url = url_list[0].get("url")
        if isinstance(url, str) and url.startswith("http") and url not in out:
            out.append(url)
    return out


def extract_work(render: dict) -> ParsedWork:
    """把 RENDER_DATA 转成统一的 ParsedWork（仅微头条图文）。"""
    if not isinstance(render, dict):
        raise PARSE_FAILED
    article = render.get("articleInfo")
    if not isinstance(article, dict):
        raise WORK_UNAVAILABLE
    thread = article.get("thread")
    if not isinstance(thread, dict):
        raise WORK_UNAVAILABLE
    base = thread.get("threadBase")
    if not isinstance(base, dict):
        raise WORK_UNAVAILABLE

    title = (
        _collapse(base.get("title"))
        or _collapse(base.get("content"))
        or _strip_html(base.get("richContent"))
        or DEFAULT_TITLE
    )

    author = DEFAULT_AUTHOR
    user = base.get("user")
    if isinstance(user, dict):
        info = user.get("info")
        if isinstance(info, dict):
            author = _collapse(info.get("name")) or author

    images = _image_urls(base)
    if not images:
        raise WORK_UNAVAILABLE

    return ParsedWork(
        type="images",
        title=title,
        author=author,
        cover_url=images[0],
        image_urls=images,
        image_groups=[[url] for url in images],
    )


async def _resolve_url(
    share_url: str, client: httpx.AsyncClient
) -> tuple[str, httpx.Response]:
    """短链跟随重定向，返回 (最终 URL, 响应)。"""
    try:
        response = await client.get(share_url, headers=PAGE_HEADERS, follow_redirects=True)
    except httpx.TimeoutException as exc:
        raise PARSE_TIMEOUT from exc
    except httpx.HTTPError as exc:
        raise PARSE_FAILED from exc
    return str(response.url), response


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        final_url, response = await _resolve_url(share_url, client)

        if _is_video_url(final_url):
            # 回流短视频网页端不提供真实视频流：明确告知需在 App 内观看
            raise VIDEO_IN_APP_ONLY

        if response.status_code in {404, 410}:
            raise WORK_UNAVAILABLE
        if response.status_code >= 400:
            if _is_risk(response.text):
                raise RISK_CONTROL
            raise PARSE_FAILED

        render = _decode_render_data(response.text)
        if render is None:
            # /w/ 页面缺 RENDER_DATA：多为已删除或形态变化
            if _is_risk(response.text):
                raise RISK_CONTROL
            raise WORK_UNAVAILABLE
        return extract_work(render)
    finally:
        if own_client:
            await client.aclose()
