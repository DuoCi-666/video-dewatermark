"""最右（xiaochuankeji.cn / izuiyou.com）分享链接解析。

平台辨识
--------
最右 = 内容社区「最右」，App ``cn.xiaochuankeji.zuiyou``，H5 分享域
``share.xiaochuankeji.cn``。与「皮皮搞笑」（``ippzone.com``，旗下另一款
App ``cn.xiaochuankeji.zuiyouLite``）**是两个不同的站**，注意区分：
- 皮皮搞笑分享域 ``h5.ippzone.com`` / 接口 ``api.ippzone.com``
- 最右分享域 ``share.xiaochuankeji.cn`` / 媒体 ``*.izuiyou.com``

链路（2026-09 实测）
--------------------
1. 分享链形如
   ``https://share.xiaochuankeji.cn/hybrid/share/post?pid=<pid>&...``，
   页面是 SSR（React），作品数据内嵌在
   ``<script id="appState">window.APP_INITIAL_STATE={...}</script>``。
2. 关键字段：``APP_INITIAL_STATE.sharePost``：
   - ``postFailure`` 非空（如 ``{"ret":-18,"msg":"帖子不存在"}``）表示作品已失效
   - ``postDetail.post`` 为作品正文（纯前端壳作品这里同样有完整媒体）

媒体结构（``postDetail.post``）
------------------------------
- **视频**：``post.videos`` 为 dict（key 为视频 id），每个元素含
  ``url``（主直链，``*.izuiyou.com`` 带短时效签名）。其他字段只有 ``dur``，
  无清晰度分档。**签名与 URL 绑定（``auth_key``），去掉会 403**，故直接用
  带签名直链，注册 ``signed_media=True`` 走失败自动重解析续期。
- **图集**：``post.imgs[]``，非视频图带 ``video:1`` 戳（是视频首帧，跳过），
  图集块在 ``urls.origin.urls[]``（``/sz/src`` 原图，多 CDN）；缩略档
  （``360/540``）不带 origin 的图记原图、否则跳过。视频帖里的 ``imgs[]``
  同样混有首帧图，故判断顺序为「有 ``videos`` 即视频，否则用非首帧
  ``imgs`` 作图集」。
- **封面**：视频取 ``videos`` 首元素的 ``url`` 对应的封面图：``imgs[]`` 中
  id 等于该视频 id 的条目（首帧图）。正常情况下它能拿到，拿不到封面不致命。

防盗链 / 直链
-------------
视频（``web-v01.izuiyou.com``）与图片（``web-f01.izuiyou.com``）CDN 实测
**不校验 Referer**，共享 client 直接可取；均支持 Range（206）。响应里的
媒体地址为 ``http://``，代理回源求稳统一升为 ``https://``（测试过可用）。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

SHARE_HOST = "share.xiaochuankeji.cn"

PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_APP_STATE_RE = re.compile(
    r'<script[^>]*id="appState"[^>]*>\s*window\.APP_INITIAL_STATE=(\{.*?\});?\s*</script>',
    re.S,
)

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁", "安全", "访问太频繁")

DEFAULT_TITLE = "最右作品"
DEFAULT_AUTHOR = "最右"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _is_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _pid_from(url: str) -> str | None:
    match = re.search(r"[?&]pid=(\d+)", url or "")
    return match.group(1) if match else None


def _to_https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def _collect_urls(urls: Any) -> list[str]:
    """从 [{"url": ...}] 或 ["url", ...] 里收集 https 直链（去重保序）。"""
    out: list[str] = []
    if isinstance(urls, str):
        urls = [urls]
    if isinstance(urls, list):
        for entry in urls:
            url = entry.get("url") if isinstance(entry, dict) else entry
            url = _to_https(url) if isinstance(url, str) else None
            if isinstance(url, str) and url.startswith("https://") and url not in out:
                out.append(url)
    return out


def _image_groups(post: dict) -> list[list[str]]:
    """图集：跳过带 video:1 戳的首帧图；命中 origin 原图档，否则跳过。

    最右的图集块固定带 ``urls.origin.urls``（多 CDN /sz/src 原图），
    缩略档（360/540）没有 origin 时无法拿到原图，直接跳过该块。
    """
    imgs = post.get("imgs")
    if not isinstance(imgs, list):
        return []
    groups: list[list[str]] = []
    for img in imgs:
        if not isinstance(img, dict) or img.get("video"):
            continue
        urls_map = img.get("urls") if isinstance(img.get("urls"), dict) else {}
        urls = _collect_urls((urls_map.get("origin") or {}).get("urls"))
        if urls:
            groups.append(urls)
    return groups


def _cover_of(post: dict) -> str | None:
    """视频封面 = 与视频同 id 的首帧图；图集封面 = 第一张原图。"""
    videos = post.get("videos")
    if isinstance(videos, dict) and videos:
        cover_ids = {str(vid_id) for vid_id in videos.keys()}
        imgs = post.get("imgs")
        if isinstance(imgs, list):
            for img in imgs:
                if not isinstance(img, dict):
                    continue
                if img.get("video") and str(img.get("id")) in cover_ids:
                    urls_map = img.get("urls") if isinstance(img.get("urls"), dict) else {}
                    urls = _collect_urls((urls_map.get("origin") or {}).get("urls"))
                    if not urls:
                        urls = _collect_urls((urls_map.get("540") or {}).get("urls"))
                    if urls:
                        return urls[0]
    groups = _image_groups(post)
    return groups[0][0] if groups else None


def _video_url(post: dict) -> str | None:
    videos = post.get("videos")
    if not isinstance(videos, dict) or not videos:
        return None
    for entry in videos.values():
        if isinstance(entry, dict):
            url = _to_https(entry.get("url"))
            if isinstance(url, str) and url.startswith("https://"):
                return url
    return None


def extract_work(state: dict) -> ParsedWork:
    """把 ``APP_INITIAL_STATE`` 里的 ``sharePost`` 转成统一的 ParsedWork。"""
    if not isinstance(state, dict):
        raise PARSE_FAILED

    failure = state.get("postFailure")
    if isinstance(failure, dict) and failure:
        message = _collapse(failure.get("msg"))
        if any(word in message for word in ("不存在", "删除", "下架", "被删", "not exist")):
            raise WORK_UNAVAILABLE
        if _is_risk(message):
            raise RISK_CONTROL
        raise PARSE_FAILED

    post_detail = state.get("postDetail")
    post = post_detail.get("post") if isinstance(post_detail, dict) else None
    if not isinstance(post, dict) or not post:
        raise WORK_UNAVAILABLE

    member = post.get("member") if isinstance(post.get("member"), dict) else {}
    author = _collapse(member.get("name")) or DEFAULT_AUTHOR
    title = _collapse(post.get("content")) or DEFAULT_TITLE
    cover = _cover_of(post)

    video_url = _video_url(post)
    if video_url:
        return ParsedWork(
            type="video",
            title=title,
            author=author,
            cover_url=cover,
            video_url=video_url,
        )

    groups = _image_groups(post)
    if groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=cover,
            image_urls=[group[0] for group in groups],
            image_groups=groups,
        )

    raise WORK_UNAVAILABLE


def _decode_app_state(html: str) -> dict | None:
    match = _APP_STATE_RE.search(html or "")
    if not match:
        return None
    raw = match.group(1).strip()
    if raw.endswith(";") and not raw.rstrip(";").endswith("{"):
        raw = raw.rstrip(";")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    pid = _pid_from(share_url)
    if pid is None:
        raise PARSE_FAILED

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=PAGE_HEADERS, timeout=timeout)
    try:
        try:
            response = await client.get(share_url, headers=PAGE_HEADERS)
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

    state = _decode_app_state(response.text)
    if not state:
        if _is_risk(response.text):
            raise RISK_CONTROL
        raise PARSE_FAILED
    share_post = state.get("sharePost")
    if not isinstance(share_post, dict):
        raise PARSE_FAILED
    return extract_work(share_post)
