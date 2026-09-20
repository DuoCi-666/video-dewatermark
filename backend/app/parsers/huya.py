"""虎牙（v.huya.com）视频分享链接解析。

平台辨识
--------
分享链接形如 ``https://v.huya.com/{vid}.html``（``vid`` 为数字）。

链路（2026-09 实测）
--------------------
调虎牙视频接口（需带站内 Referer）：
``https://liveapi.huya.com/moment/getMomentContent?videoId={vid}``
返回 ``data.moment.videoInfo``：
- ``videoTitle`` 标题、``videoCover`` 封面；
- ``definitions[]`` 多档位直链（``definitions[0].url`` 通常最高画质），
  回退 ``videoUrl``；
- ``nickName`` 作者、``uid`` 作者 id、``avatarUrl`` 头像。

.. note::
   直链为 ``*.cdn.huya.com`` 的 mp4，实测可 206 拉流（``video/mp4``），
   需带 Referer``https://v.huya.com/``。
"""
from __future__ import annotations

import re

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

API_URL = "https://liveapi.huya.com/moment/getMomentContent"
_VID_RE = re.compile(r"/(\d+)\.html")

DEFAULT_TITLE = "虎牙视频"
DEFAULT_AUTHOR = "虎牙"


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_vid(url: str) -> str | None:
    match = _VID_RE.search(url or "")
    return match.group(1) if match else None


def extract_work(data: dict) -> ParsedWork:
    """把 videoInfo 转成统一的 ParsedWork。"""
    if not isinstance(data, dict) or int(data.get("status") or 0) != 200:
        raise PARSE_FAILED

    moment = (data.get("data") or {}).get("moment") if isinstance(data.get("data"), dict) else None
    info = moment.get("videoInfo") if isinstance(moment, dict) else None
    if not isinstance(info, dict):
        raise PARSE_FAILED

    video_url = None
    definitions = info.get("definitions")
    if isinstance(definitions, list):
        for item in definitions:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    video_url = url
                    break
    if not video_url:
        fallback = info.get("videoUrl")
        if isinstance(fallback, str) and fallback.startswith("http"):
            video_url = fallback
    if not video_url:
        raise WORK_UNAVAILABLE

    cover = info.get("videoCover")
    return ParsedWork(
        type="video",
        title=_collapse(info.get("videoTitle")) or DEFAULT_TITLE,
        author=_collapse(info.get("nickName")) or DEFAULT_AUTHOR,
        cover_url=cover if isinstance(cover, str) and cover.startswith("http") else None,
        video_url=video_url,
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    vid = extract_vid(share_url)
    if not vid:
        raise PARSE_FAILED

    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://v.huya.com/",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            resp = await client.get(
                API_URL,
                params={"videoId": vid},
                headers=headers,
                follow_redirects=True,
            )
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
        except ValueError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    return extract_work(data)
