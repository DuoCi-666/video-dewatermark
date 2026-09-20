"""AcFun（A站，acfun.cn）视频分享链接解析。

平台辨识
--------
视频页形如 ``https://www.acfun.cn/v/ac48851115``（``ac`` + 数字内容 id）。
移动站 ``m.acfun.cn`` 为前端壳、不含作品数据，故本解析器使用**桌面 UA**
请求 PC 页（PC 页对桌面 UA 直接 200，不做移动端跳转）。

链路（2026-09 实测）
--------------------
1. GET 视频页 HTML，页面内嵌 ``window.pageInfo = window.videoInfo = {...}``，
   取其中 JSON（大括号配对提取）：
   - ``title`` 标题、``coverUrl`` 封面、``currentVideoId`` 当前视频 id。
2. 播放地址在 ``window.videoInfo.currentVideoInfo.ksPlayJson``（**JSON 字符串**，
   需二次 ``json.loads``）里的 ``adaptationSet[].representation[]``：
   - ``url`` 播放地址（**HLS m3u8**，带 ``pkey`` 短时效签名）；
   - ``backupUrl[]`` 同档位的备用 CDN；
   - ``qualityLabel`` / ``qualityType`` 清晰度（如 ``1080P+`` / ``1080P`` / ``720P``），
     数组顺序即从高到低。

媒体策略
--------
每个 ``representation`` 映射为一个清晰度档位（``variants``，顺序即从高到低），
首选档位（``variants[0]``）作为 ``video_url``，同档位备用 CDN 作为 ``video_fallbacks``。

.. note::
   A站源为 **HLS（m3u8）**，且直链带 ``pkey`` 短时效签名（注册 ``signed_media=True``，
   失效由媒体代理自动重解析续期）；前端播放 m3u8 需 hls.js 支持。

防盗链
------
``tx-safety-video.acfun.cn`` 实测不校验 Referer，直链可直接取。
"""
from __future__ import annotations

import json

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

DEFAULT_TITLE = "AcFun 视频"
DEFAULT_AUTHOR = "AcFun"


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_video_info(html: str) -> dict | None:
    """提取 ``window.videoInfo = {...}`` 的 JSON（大括号配对，容忍嵌套与转义）。"""
    idx = (html or "").find("window.videoInfo")
    if idx < 0:
        return None
    start = html.find("{", idx)
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


def _representations(video_info: dict) -> list[dict]:
    """从 ``currentVideoInfo.ksPlayJson`` 里取出各清晰度的 representation。"""
    cvi = video_info.get("currentVideoInfo")
    if not isinstance(cvi, dict):
        return []
    raw = cvi.get("ksPlayJson")
    if not isinstance(raw, str) or not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for item in data.get("adaptationSet") or []:
        if not isinstance(item, dict):
            continue
        reps = item.get("representation")
        if isinstance(reps, list):
            out.extend(rep for rep in reps if isinstance(rep, dict))
    return out


def _variant_urls(rep: dict) -> list[str]:
    """主 url 在前，同档位备用 CDN 在后。"""
    urls: list[str] = []
    primary = rep.get("url")
    if isinstance(primary, str) and primary.startswith("http"):
        urls.append(primary)
    for backup in rep.get("backupUrl") or []:
        if isinstance(backup, str) and backup.startswith("http") and backup not in urls:
            urls.append(backup)
    return urls


def extract_work(video_info: dict) -> ParsedWork:
    """把 ``window.videoInfo`` 转成统一的 ParsedWork。"""
    if not isinstance(video_info, dict):
        raise PARSE_FAILED

    title = _collapse(video_info.get("title")) or DEFAULT_TITLE
    cover = video_info.get("coverUrl")
    cover_url = cover if isinstance(cover, str) and cover.startswith("http") else None

    variants: list[VideoVariant] = []
    for rep in _representations(video_info):
        urls = _variant_urls(rep)
        if not urls:
            continue
        label = str(rep.get("qualityLabel") or rep.get("qualityType") or "").strip() or "默认"
        variants.append(VideoVariant(label=label, rank=len(variants), urls=urls))

    if not variants:
        raise WORK_UNAVAILABLE

    return ParsedWork(
        type="video",
        title=title,
        author=DEFAULT_AUTHOR,
        cover_url=cover_url,
        video_url=variants[0].urls[0],
        video_fallbacks=variants[0].urls[1:],
        variants=variants,
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    headers = dict(PAGE_HEADERS)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            page = await client.get(share_url, follow_redirects=True, headers=headers)
            video_info = extract_video_info(page.text)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if not video_info:
        raise PARSE_FAILED
    return extract_work(video_info)
