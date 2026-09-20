"""腾讯视频（v.qq.com）视频分享链接解析。

平台辨识
--------
分享链接形如：
- PC 单集：``https://v.qq.com/x/page/{vid}.html``
- PC 专辑单集：``https://v.qq.com/x/cover/{cid}/{vid}.html``
- 移动端：``https://m.v.qq.com/x/m/play?vid={vid}``
``vid`` 形如 ``c0329j2hqcf``（字母 + 数字）。

.. note::
   专辑/剧集主页 ``https://v.qq.com/x/cover/{cid}.html``（**只有一段路径**）没有
   具体单集，会返回明确的引导错误，而不是笼统报解析失败。

链路（2026-09 实测）
--------------------
1. 从链接提取 ``vid``（移动端取 ``vid`` 查询参数，PC 端从路径正则提取）。
2. 调视频信息接口（**JSONP**）：
   ``https://vv.video.qq.com/getinfo?vids={vid}&platform=101001&otype=json&defn=shd``
   返回 ``QZOutputJson={...};``，去掉前缀与尾分号后解析：
   - ``em`` 为 ``0`` 才算成功（非 0 是内容不可观看的错误码）；
   - ``vl.vi[0]``：``ti`` 标题、``fn`` 文件名、``fvkey`` 播放密钥；
   - ``vl.vi[0].ul.ui[0].url`` 为 CDN 基址。
3. 拼接播放直链：``{ul.ui[0].url}{fn}?vkey={fvkey}``（实测可 206 断点拉流，``video/mp4``）。

封面
----
``https://puui.qpic.cn/vpic_cover/{vid}/{vid}_hz.jpg/496``（实测 200）。

.. note::
   直链带 ``fvkey``（时效签名），故注册 ``signed_media=True``，失效由媒体代理
   自动重解析换新地址。腾讯视频**长视频正片多为 VIP 内容**，需登录态才可解析；
   免费/公开内容（部分短片、综艺片段、自制内容）可直接解析。
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE, ParseError
from app.parsers.kuaishou import ParsedWork

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# /x/page/{vid}.html 与 /x/cover/{cid}/{vid}.html（后者要求两段路径）
_PAGE_VID_RE = re.compile(r"/x/page/(\w+)\.html")
_COVER_VID_RE = re.compile(r"/x/cover/[\w-]+/(\w+)\.html")
# 专辑/剧集主页：只有 /x/cover/{cid}.html 一段路径，不含具体单集
_COVER_ONLY_RE = re.compile(r"/x/cover/[\w-]+\.html$")

API_URL = "https://vv.video.qq.com/getinfo"
JSONP_PREFIX = "QZOutputJson="

DEFAULT_TITLE = "腾讯视频"
DEFAULT_AUTHOR = "腾讯视频"

# 专辑/剧集页（非具体单集）：给用户可执行的引导，与真正的解析失败区分开
SERIES_PAGE = ParseError(
    422, "PROFILE_LINK", "这是专辑/剧集页，请进入具体单集后再复制分享链接"
)


def _collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def extract_vid(url: str) -> str | None:
    """从分享链接提取 vid；专辑主页等非单集链接返回 None。"""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.endswith("m.v.qq.com"):
        vid = (parse_qs(parsed.query).get("vid") or [""])[0]
        return vid or None
    path = parsed.path or ""
    match = _COVER_VID_RE.search(path) or _PAGE_VID_RE.search(path)
    return match.group(1) if match else None


def is_series_page(url: str) -> bool:
    """是否为专辑/剧集主页（``/x/cover/{cid}.html``）。"""
    try:
        path = urlparse(url or "").path or ""
    except ValueError:
        return False
    return bool(_COVER_ONLY_RE.search(path))


def parse_jsonp(text: str) -> dict | None:
    """去掉 JSONP 前缀 ``QZOutputJson=`` 与尾分号后解析 JSON。"""
    raw = (text or "").strip()
    if raw.startswith(JSONP_PREFIX):
        raw = raw[len(JSONP_PREFIX):]
    raw = raw.rstrip(";").strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _first_vi(data: dict) -> dict | None:
    vl = data.get("vl")
    if not isinstance(vl, dict):
        return None
    vi = vl.get("vi")
    if isinstance(vi, list) and vi and isinstance(vi[0], dict):
        return vi[0]
    return None


def extract_work(data: dict) -> ParsedWork:
    """把 ``getinfo`` 的返回转成统一的 ParsedWork。"""
    if not isinstance(data, dict):
        raise PARSE_FAILED

    try:
        em = int(data.get("em") or 0)
    except (TypeError, ValueError):
        em = -1
    if em != 0:
        raise WORK_UNAVAILABLE

    vi = _first_vi(data)
    if not vi:
        raise WORK_UNAVAILABLE

    ul = vi.get("ul")
    ui = ul.get("ui") if isinstance(ul, dict) else None
    ui0 = ui[0] if isinstance(ui, list) and ui and isinstance(ui[0], dict) else {}
    base = ui0.get("url")
    fn = vi.get("fn")
    vkey = vi.get("fvkey")
    if not (
        isinstance(base, str)
        and base.startswith("http")
        and isinstance(fn, str)
        and fn
        and isinstance(vkey, str)
        and vkey
    ):
        raise PARSE_FAILED

    vid = vi.get("vid") if isinstance(vi.get("vid"), str) else ""
    title = _collapse(vi.get("ti")) or DEFAULT_TITLE
    cover_url = f"https://puui.qpic.cn/vpic_cover/{vid}/{vid}_hz.jpg/496" if vid else None

    return ParsedWork(
        type="video",
        title=title,
        author=DEFAULT_AUTHOR,
        cover_url=cover_url,
        video_url=f"{base}{fn}?vkey={vkey}",
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    vid = extract_vid(share_url)
    if not vid:
        if is_series_page(share_url):
            raise SERIES_PAGE
        raise PARSE_FAILED

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout)
    try:
        try:
            resp = await client.get(
                API_URL,
                params={"vids": vid, "platform": "101001", "otype": "json", "defn": "shd"},
                headers=headers,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    data = parse_jsonp(resp.text)
    if not data:
        raise PARSE_FAILED
    return extract_work(data)
