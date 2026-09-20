"""好看视频（haokan.baidu.com / haokan.hao123.com）分享链接解析。

平台辨识
--------
分享链接常见形态：
- ``https://haokan.hao123.com/v?vid={vid}``
- ``https://haokan.baidu.com/v?vid={vid}``
- ``https://sv.baidu.com/videoui/page/videoland?context={"nid":"sv_{vid}"}``

``vid`` 为纯数字。PC UA 会被风控成错误页，移动 UA + JSON 接口可直取。

链路（2026-09 实测）
--------------------
``GET https://haokan.baidu.com/haokan/ui-web/video/detail?vid={vid}&_format=json``
返回 ``status=0`` 时，作品在 ``data.apiData.curVideoMeta``：
- ``title`` 标题；
- ``poster`` 封面；
- ``clarityUrl[]`` 多档直链（``key``/``rank``/``title``/``url``，如标清 sd / 超清 sc）；
- 作者可从 ``header.description`` 的「本视频由{作者}原创提供」提取。

直链带 ``auth_key`` 短时效签名，失效后由媒体代理重新解析换新地址。
CDN（``*.bdstatic.com``）支持 Range / 206，不强制 Referer。
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

API_URL = "https://haokan.baidu.com/haokan/ui-web/video/detail"
REFERER = "https://haokan.baidu.com/"

_VID_QUERY_RE = re.compile(r"(?:^|[?&])vid=(\d+)")
_SV_NID_RE = re.compile(r"sv_(\d+)")
_AUTHOR_RE = re.compile(r"本视频由(.+?)原创提供")

DEFAULT_TITLE = "好看视频"
DEFAULT_AUTHOR = "好看视频"

QUALITY_RANK = {
    "ld": 1,
    "sd": 2,
    "hd": 3,
    "sc": 4,
    "fhd": 5,
    "4k": 6,
}


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_vid(url: str) -> str | None:
    """从分享链接提取好看视频 vid。"""
    raw = url or ""
    match = _VID_QUERY_RE.search(raw)
    if match:
        return match.group(1)
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    query = parse_qs(parsed.query or "")
    vid = (query.get("vid") or [None])[0]
    if isinstance(vid, str) and vid.isdigit():
        return vid
    context = (query.get("context") or [""])[0]
    if context:
        decoded = unquote(context)
        nid = _SV_NID_RE.search(decoded)
        if nid:
            return nid.group(1)
        try:
            payload = json.loads(decoded)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            nid_val = str(payload.get("nid") or "")
            nid = _SV_NID_RE.search(nid_val)
            if nid:
                return nid.group(1)
    return None


def _author_from_header(header: object) -> str:
    if not isinstance(header, dict):
        return ""
    desc = _collapse(header.get("description"))
    match = _AUTHOR_RE.search(desc)
    return _collapse(match.group(1)) if match else ""


def _http_url(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    return None


def extract_work(data: dict) -> ParsedWork:
    """把 video/detail JSON 转成统一的 ParsedWork。"""
    if not isinstance(data, dict):
        raise PARSE_FAILED
    try:
        status = int(data.get("status"))
    except (TypeError, ValueError):
        status = -1
    if status != 0:
        raise PARSE_FAILED

    payload = data.get("data")
    api = payload.get("apiData") if isinstance(payload, dict) else None
    if not isinstance(api, dict):
        raise PARSE_FAILED
    meta = api.get("curVideoMeta")
    if not isinstance(meta, dict):
        raise PARSE_FAILED

    variants: list[VideoVariant] = []
    seen: set[str] = set()
    clarity = meta.get("clarityUrl")
    if isinstance(clarity, list):
        for item in clarity:
            if not isinstance(item, dict):
                continue
            url = _http_url(item.get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            key = str(item.get("key") or "").lower()
            label = _collapse(item.get("title")) or key.upper() or "标清"
            try:
                rank = int(item.get("rank") or 0)
            except (TypeError, ValueError):
                rank = 0
            rank = QUALITY_RANK.get(key, rank)
            variants.append(VideoVariant(label=label, rank=rank, urls=[url]))

    fallback = _http_url(meta.get("playurl"))
    if fallback and fallback not in seen:
        variants.append(VideoVariant(label="标清", rank=QUALITY_RANK["sd"], urls=[fallback]))

    variants.sort(key=lambda item: item.rank, reverse=True)
    if not variants:
        raise WORK_UNAVAILABLE

    cover = _http_url(meta.get("poster")) or _http_url(meta.get("poster_big"))
    author = _author_from_header(api.get("header")) or DEFAULT_AUTHOR
    return ParsedWork(
        type="video",
        title=_collapse(meta.get("title")) or DEFAULT_TITLE,
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
    vid = extract_vid(share_url)
    if not vid:
        raise PARSE_FAILED

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": REFERER,
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            resp = await client.get(
                API_URL,
                params={"vid": vid, "_format": "json"},
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
