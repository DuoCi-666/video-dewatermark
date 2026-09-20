"""搜狐视频（tv.sohu.com）视频分享链接解析。

平台辨识
--------
分享链接常见两种形态：
- ``https://tv.sohu.com/v/{base64}.html`` —— 路径中的 base64 解码后是
  ``us/{uid}/{vid}.shtml``；
- ``https://my.tv.sohu.com/us/{uid}/{vid}.shtml``（或 ``tv.sohu.com/us/...``）——
  直接含 ``us/{uid}/{vid}``。
两种都能取出数字 ``vid``。

链路（2026-09 实测）
--------------------
调搜狐视频信息 API（带固定 ``api_key``）：
``https://api.tv.sohu.com/v4/video/info/{vid}.json?site=2&api_key=9854b2afa779e1a6bcdd07b217417549&sver=6.2.0``
返回 JSON：
- ``status`` 为 ``200`` 才算成功；
- ``data.url_high_mp4``（首选高清 MP4 直链，回退 ``download_url``）；
- ``data.video_name`` 标题、``data.originalCutCover`` 封面；
- ``data.user.nickname`` 作者。

.. note::
   直链为 ``http://data.vod.itc.cn/?k=...`` 形式，实测可直接取（``video/mp4``）。
"""
from __future__ import annotations

import base64
import re
from urllib.parse import urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

API_KEY = "9854b2afa779e1a6bcdd07b217417549"
API_URL = "https://api.tv.sohu.com/v4/video/info/{vid}.json"

_B64_PATH_RE = re.compile(r"/v/([A-Za-z0-9+/=]+)\.html")
_USER_VID_RE = re.compile(r"/?us/\d+/(\d+)\.shtml")

DEFAULT_TITLE = "搜狐视频"
DEFAULT_AUTHOR = "搜狐视频"


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def _decode_b64(raw: str) -> str:
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded).decode("utf-8", "ignore")
    except (ValueError, TypeError):
        return ""


def extract_vid(url: str) -> str | None:
    """从分享链接提取搜狐视频数字 vid。"""
    try:
        path = urlparse(url or "").path or ""
    except ValueError:
        return None
    match = _B64_PATH_RE.search(path)
    if match:
        decoded = _decode_b64(match.group(1))
        inner = _USER_VID_RE.search(decoded)
        if inner:
            return inner.group(1)
    match = _USER_VID_RE.search(path)
    return match.group(1) if match else None


def extract_work(data: dict) -> ParsedWork:
    """把搜狐视频信息 API 的返回转成统一的 ParsedWork。"""
    if not isinstance(data, dict):
        raise PARSE_FAILED
    try:
        status = int(data.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise PARSE_FAILED

    payload = data.get("data")
    if not isinstance(payload, dict):
        raise PARSE_FAILED

    video_url = payload.get("url_high_mp4") or payload.get("download_url")
    if not (isinstance(video_url, str) and video_url.startswith("http")):
        raise WORK_UNAVAILABLE
    # url_high_mp4 / download_url 是逗号分隔的多 CDN 候选串（实测可达 18 个）；
    # 整串塞进 video_url 会被媒体代理当作单个 URL 回源而 404，拆成主地址 + 备用列表。
    candidates = [u.strip() for u in video_url.split(",") if u.strip().startswith("http")]
    if not candidates:
        raise WORK_UNAVAILABLE

    cover = payload.get("originalCutCover")
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    author = _collapse(user.get("nickname")) or DEFAULT_AUTHOR

    return ParsedWork(
        type="video",
        title=_collapse(payload.get("video_name")) or DEFAULT_TITLE,
        author=author,
        cover_url=cover if isinstance(cover, str) and cover.startswith("http") else None,
        video_url=candidates[0],
        video_fallbacks=candidates[1:],
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    vid = extract_vid(share_url)
    if not vid:
        raise PARSE_FAILED

    headers = {"User-Agent": UA, "Accept": "*/*"}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            resp = await client.get(
                API_URL.format(vid=vid),
                params={"site": "2", "api_key": API_KEY, "sver": "6.2.0"},
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
