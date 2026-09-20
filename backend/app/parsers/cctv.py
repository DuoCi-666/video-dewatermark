"""央视网（cctv.com / cntv.cn）视频分享链接解析。

平台辨识
--------
央视网视频页域名形如 ``tv.cctv.com`` / ``news.cctv.com`` / ``v.cctv.com``，
以及央视网自有域 ``cntv.cn``。分享链接一般直接指向具体视频页，例如
``https://tv.cctv.com/2026/09/16/VIDExxxxxxxx.shtml``。

链路（2026-09 实测）
--------------------
1. 请求视频页 HTML（iPhone UA），正则 ``var guid = "<32位哈希>"`` 取出视频 GUID。
2. 调央视网视频信息 API：
   ``https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=<guid>``，返回 JSON：
   - ``status`` 为 ``"001"`` 才算成功；
   - ``title`` 标题、``image`` 封面、``play_channel`` 频道（用作作者）；
   - ``video.chapters[]`` 分片 MP4 直链（新闻片段等短视频为单段）；
   - ``hls_url`` HLS 播放列表。

媒体策略
--------
优先取 ``video.chapters`` 中非空的 MP4 直链（短视频即完整原片）；
取不到时回退 ``hls_url``（m3u8）。

.. note::
   央视网画面**带台标**（非平台水印，如实呈现）；整期长节目（如《新闻联播》）
   的分片 MP4 直链常为空，只能拿到 ``hls_url``（HLS 流，前端播放需 hls.js 支持）。

防盗链
------
``vod.cntv.lxdns.com`` 实测**不校验 Referer**，直链可直接取。
"""
from __future__ import annotations

import re

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 页面内嵌的视频 GUID：var guid = "2411c9fc8c724d9ba224f97f825adddd"
_GUID_RE = re.compile(r'var\s+guid\s*=\s*"([^"]+)"')

API_URL = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do"

DEFAULT_TITLE = "央视网视频"
DEFAULT_AUTHOR = "央视网"


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_guid(html: str) -> str | None:
    """从视频页 HTML 中提取 GUID。"""
    match = _GUID_RE.search(html or "")
    return match.group(1) if match else None


def _media_url(payload: dict) -> str | None:
    """优先分片 MP4 直链，回退 HLS 播放列表。"""
    video = payload.get("video")
    if isinstance(video, dict):
        chapters = video.get("chapters")
        if isinstance(chapters, list):
            for chapter in chapters:
                if not isinstance(chapter, dict):
                    continue
                url = chapter.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
    hls = payload.get("hls_url")
    if isinstance(hls, str) and hls.startswith("http"):
        return hls
    return None


def extract_work(payload: dict) -> ParsedWork:
    """把央视网视频信息 API 的返回转成统一的 ParsedWork。"""
    if not isinstance(payload, dict) or str(payload.get("status")) != "001":
        raise PARSE_FAILED

    title = _collapse(payload.get("title")) or DEFAULT_TITLE
    author = _collapse(payload.get("play_channel")) or DEFAULT_AUTHOR
    cover = payload.get("image")
    cover_url = cover if isinstance(cover, str) and cover.startswith("http") else None

    video_url = _media_url(payload)
    if not video_url:
        raise WORK_UNAVAILABLE

    return ParsedWork(
        type="video",
        title=title,
        author=author,
        cover_url=cover_url,
        video_url=video_url,
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
            guid = extract_guid(page.text)
            if not guid:
                raise PARSE_FAILED
            resp = await client.get(
                API_URL, params={"pid": guid}, headers=headers, follow_redirects=True
            )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise PARSE_FAILED from exc
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    return extract_work(payload)
