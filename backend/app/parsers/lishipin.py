"""梨视频（pearvideo.com）视频分享链接解析。

平台辨识
--------
分享链接形如 ``https://www.pearvideo.com/detail_{contId}``（也有 ``/video_{contId}``
形态），``contId`` 为数字。

链路（2026-09 实测）
--------------------
调梨视频视频信息接口（**必须带站内 Referer**）：
``https://www.pearvideo.com/videoStatus.jsp?contId={contId}&mrd={毫秒时间戳}``
返回 JSON：
- ``systemTime``：服务端当前毫秒时间戳；
- ``videoInfo.videos.srcUrl``：直链，形如
  ``https://video.pearvideo.com/mp4/short/{日期}/{systemTime}-{hash}-hd.mp4``；
- ``videoInfo.video_image``：封面。

**关键**：把 ``srcUrl`` 中的 ``systemTime`` 替换成 ``cont-{contId}`` 才是无水印可下载
地址（原样带时间戳的地址会 403）；替换后实测可 206 拉流，``video/mp4``。

.. note::
   该接口不返回标题，故标题回退默认值。
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

API_URL = "https://www.pearvideo.com/videoStatus.jsp"
_CONT_ID_RE = re.compile(r"/(?:detail|video)_(\d+)")

DEFAULT_TITLE = "梨视频"
DEFAULT_AUTHOR = "梨视频"


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_cont_id(url: str) -> str | None:
    """从分享链接提取梨视频作品 contId。"""
    try:
        path = urlparse(url or "").path or ""
    except ValueError:
        return None
    match = _CONT_ID_RE.search(path)
    return match.group(1) if match else None


def extract_work(data: dict, cont_id: str) -> ParsedWork:
    """把 ``videoStatus`` 的返回转成统一的 ParsedWork。"""
    if not isinstance(data, dict) or str(data.get("resultCode")) != "1":
        raise PARSE_FAILED

    info = data.get("videoInfo")
    if not isinstance(info, dict):
        raise PARSE_FAILED
    videos = info.get("videos") if isinstance(info.get("videos"), dict) else {}
    src = videos.get("srcUrl")
    if not (isinstance(src, str) and src.startswith("http")):
        raise WORK_UNAVAILABLE

    # 带时间戳的原地址会 403，替换成 cont-{contId} 才是无水印可下载地址
    timer = data.get("systemTime")
    if isinstance(timer, str) and timer:
        src = src.replace(timer, f"cont-{cont_id}")

    cover = info.get("video_image")
    return ParsedWork(
        type="video",
        title=DEFAULT_TITLE,
        author=DEFAULT_AUTHOR,
        cover_url=cover if isinstance(cover, str) and cover.startswith("http") else None,
        video_url=src,
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    cont_id = extract_cont_id(share_url)
    if not cont_id:
        raise PARSE_FAILED

    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        # 接口强制校验站内 Referer
        "Referer": f"https://www.pearvideo.com/detail_{cont_id}",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            resp = await client.get(
                API_URL,
                params={"contId": cont_id, "mrd": int(time.time() * 1000)},
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

    return extract_work(data, cont_id)
