"""微信公众号（mp.weixin.qq.com）图文解析。

平台辨识
--------
公众号文章链接形如 ``https://mp.weixin.qq.com/s/<token>``，群发/历史链接也可能带
``__biz`` / ``mid`` / ``idx`` / ``sn`` 等参数，但文章域始终是 ``mp.weixin.qq.com``。

链路（2026-09 实测）
--------------------
公众号文章页是**服务端渲染**，正文在 ``<div id="js_content">`` 里：

- 标题：``var msg_title = "..."``，或 ``<h1 class="rich_media_title" id="activity-name">``。
- 公众号名：``var nickname = "..."``，或 ``#js_name``。
- 正文图片：``<img data-src="https://mmbiz.qpic.cn/mmbiz_xxx/...">``。
  注意微信用 **data-src** 做懒加载（不是 src），地址常带 ``?wx_fmt=jpeg`` 等查询串，
  直链在浏览器里无需 Referer 即可打开，是无水印原图。
- 文中视频：``<iframe class="video_iframe" data-src="...&vid=wxv_<id>&...">``。
  拿到 ``vid`` 后请求 ``/mp/readtemplate?t=pages/video_player_tmpl&action=getmpvideo&vid=<vid>``
  可换出 mp4 直链。

取舍：公众号以图文为主，本解析器默认输出**正文图片**（type=images）；
仅当一篇文章既无图片又含可解析视频时，才退化为视频（type=video）。
"""
from __future__ import annotations

import re

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 视频直链换取接口（传入 vid=wxv_xxx）
VIDEO_API = (
    "https://mp.weixin.qq.com/mp/readtemplate"
    "?t=pages/video_player_tmpl&action=getmpvideo&vid={vid}"
)

# data-src 正文图片（mmbiz.qpic.cn / mmecoa 等）
_IMG_RE = re.compile(
    r'<img[^>]+data-src="(https?://mmbiz[^"]+)"', re.I
)
# 视频 iframe 里的 vid=wxv_xxx
_VID_RE = re.compile(r"vid=(wxv_[0-9A-Za-z]+)")
# 内联 JS 变量：var msg_title = "...";  var nickname = "...";
_VAR_TITLE_RE = re.compile(r"var\s+msg_title\s*=\s*['\"](.+?)['\"]\s*;", re.S)
_VAR_NICK_RE = re.compile(r"var\s+nickname\s*=\s*['\"](.+?)['\"]\s*;", re.S)
# h1 标题 / #js_name（HTML 里常带空白与多余标签）
_H1_RE = re.compile(
    r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', re.S
)
_JSNAME_RE = re.compile(
    r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', re.S
)

DELETED_HINTS = (
    "该内容已被发布者删除",
    "该内容已被群发删除",
    "此内容因违规无法查看",
    "该公众号已迁移",
    "此账号已自主注销",
)
RISK_HINTS = ("环境异常", "验证", "访问过于频繁", "参数错误", "链接已过期")

DEFAULT_TITLE = "微信公众号文章"
DEFAULT_AUTHOR = "微信公众号"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _strip_tags(html: str) -> str:
    return _collapse(re.sub(r"<[^>]+>", "", html or ""))


def _unquote_js(text: str) -> str:
    """JS 字符串里常见 \\x 转义与实体，做一层轻量还原。"""
    text = (text or "").strip()
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    return _collapse(text)


def _title(html: str) -> str:
    m = _VAR_TITLE_RE.search(html)
    if m:
        return _unquote_js(m.group(1)) or DEFAULT_TITLE
    m = _H1_RE.search(html)
    if m:
        return _strip_tags(m.group(1)) or DEFAULT_TITLE
    return DEFAULT_TITLE


def _author(html: str) -> str:
    m = _VAR_NICK_RE.search(html)
    if m:
        return _unquote_js(m.group(1)) or DEFAULT_AUTHOR
    m = _JSNAME_RE.search(html)
    if m:
        return _strip_tags(m.group(1)) or DEFAULT_AUTHOR
    return DEFAULT_AUTHOR


def _article_images(html: str) -> list[str]:
    """按出现顺序收集正文图片，去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for url in _IMG_RE.findall(html):
        url = url.replace("&amp;", "&")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _video_vid(html: str) -> str | None:
    m = _VID_RE.search(html)
    return m.group(1) if m else None


async def _resolve_video(vid: str, client: httpx.AsyncClient) -> str | None:
    """用 vid 换 mp4 直链；失败返回 None（不影响图片结果）。"""
    try:
        resp = await client.get(VIDEO_API.format(vid=vid), headers=PAGE_HEADERS)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    if not url and isinstance(data.get("video"), dict):
        url = data["video"].get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return None


async def parse(
    url: str,
    *,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers=PAGE_HEADERS, timeout=httpx.Timeout(timeout), follow_redirects=True
        )
    try:
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc

        html = resp.text or ""
        if resp.status_code in {404, 410}:
            raise WORK_UNAVAILABLE
        if any(h in html for h in DELETED_HINTS):
            raise WORK_UNAVAILABLE
        if any(h in html for h in RISK_HINTS) or resp.status_code >= 500:
            raise RISK_CONTROL

        title = _title(html)
        author = _author(html)
        images = _article_images(html)

        if images:
            return ParsedWork(
                type="images",
                title=title,
                author=author,
                cover_url=images[0],
                image_urls=images,
                image_groups=[[u] for u in images],
            )

        # 无正文图片：退化为文中视频
        vid = _video_vid(html)
        if vid:
            video_url = await _resolve_video(vid, client)
            if video_url:
                return ParsedWork(
                    type="video",
                    title=title,
                    author=author,
                    video_url=video_url,
                    variants=[VideoVariant(label="默认", rank=1, urls=[video_url])],
                )

        raise WORK_UNAVAILABLE
    finally:
        if own_client:
            await client.aclose()
