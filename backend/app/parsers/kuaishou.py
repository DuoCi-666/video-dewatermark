from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any
import httpx

from urllib.parse import urlparse

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, PROFILE_LINK, RISK_CONTROL, WORK_UNAVAILABLE

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

ASSET_HOST_HINTS = ("yximgs.com", "kwimgs.com", "kwaicdn.com", "oskwai.com", "kuaishou.com")

RISK_HINTS_LOWER = ("captcha", "防水墙")
RISK_HINTS_EXACT = ("安全验证", "滑动验证", "验证码")

# 主页落地页：分享口令有时指向「作者主页」而非具体作品（典型：快手极速版口令的
# v.kuaishou.com 短链 302 到 /fw/user/<encId>）。这类页面没有单一作品数据，提前
# 识别成 PROFILE_LINK 给出可执行的引导，而不是让解析器空手而归报 PARSE_FAILED。
PROFILE_HOSTS = ("kuaishou.com", "kuaishouapp.com", "gifshow.com", "chenzhongtech.com")
PROFILE_PATH_PREFIXES = ("/fw/user/", "/profile/")


def _is_profile_url(url: str) -> bool:
    """判断 URL 是否为快手作者主页（m 站 /fw/user/ 与 www 站 /profile/）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not any(host == h or host.endswith("." + h) for h in PROFILE_HOSTS):
        return False
    path = parsed.path or ""
    return any(path.startswith(prefix) and len(path) > len(prefix) for prefix in PROFILE_PATH_PREFIXES)

# 匹配媒体直链：排除引号/反斜杠/空白/尖括号（修复原 \\s 误排除字母 s 的 bug）
MEDIA_URL_RE = r"""https://[^"'\\\s>]+\.(?:mp4|jpg|jpeg|png|webp)[^"'\\\s>]*"""


@dataclass
class VideoVariant:
    """一个清晰度档位：label 展示名，urls 为该档位的多 CDN 候选（首选在前）。"""

    label: str
    rank: int
    urls: list[str] = field(default_factory=list)


@dataclass
class ParsedWork:
    type: str
    title: str
    author: str
    cover_url: str | None = None
    video_url: str | None = None
    video_fallbacks: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    image_groups: list[list[str]] = field(default_factory=list)
    variants: list[VideoVariant] = field(default_factory=list)


def _balanced_json(text: str, start: int) -> Any | None:
    i = start
    while i < len(text) and text[i] in " \n\r\t:=":
        i += 1
    if i >= len(text) or text[i] not in "{[":
        return None
    opener = text[i]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        ch = text[j]
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
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _first_json_after(text: str, key: str) -> Any | None:
    idx = text.find(key)
    if idx < 0:
        return None
    bracket = text.find("{", idx)
    square = text.find("[", idx)
    candidates = [p for p in (bracket, square) if p >= 0]
    if not candidates:
        return None
    return _balanced_json(text, min(candidates))


def _pick_url(items: Any) -> str | None:
    if isinstance(items, str) and items.startswith("http"):
        return items
    if isinstance(items, dict):
        for key in ("url", "src", "photoUrl", "coverUrl"):
            value = items.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        return None
    if isinstance(items, list):
        for item in items:
            url = _pick_url(item)
            if url:
                return url
    return None


def _collect_urls(items: Any) -> list[str]:
    """递归收集子树内全部 http URL（多 CDN 候选），而非只取第一个。"""
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("http"):
                out.append(node)
        elif isinstance(node, dict):
            hit = False
            for key in ("url", "src", "photoUrl", "coverUrl"):
                value = node.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    out.append(value)
                    hit = True
            if not hit:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(items)
    return out


def _unescape_caption(value: str) -> str:
    return unescape(value.replace("\\n", " ").replace("\\/", "/")).strip()


def _first_string(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return _unescape_caption(match.group(1))


def _rank_urls(urls: list[str]) -> list[str]:
    """按清晰度特征排序，高分在前；同档位短 URL 优先。"""
    if not urls:
        return []

    def rank(url: str) -> tuple[int, int]:
        if "hd15" in url or "tt=hd" in url:
            return (0, -len(url))
        if "_b_" in url or "tt=b" in url:
            return (2, -len(url))
        return (1, -len(url))

    return sorted(urls, key=rank)


def _classify_quality(url: str) -> int:
    """按实测特征分档：0=高清(hd15 等)、2=标清(_b_/tt=b)、1=默认。"""
    lower = url.lower()
    path = lower.split("?")[0]
    if "_b_" in path or "tt=b" in lower:
        return 2
    if re.search(r"_hd\d+", path) or "hd15" in path:
        return 0
    return 1


def _group_variants(urls: list[str]) -> list[VideoVariant]:
    """把全部候选 URL 按清晰度分组，输出有清晰次序的档位列表（每档多 CDN）。"""
    buckets: dict[int, list[str]] = {}
    for url in urls:
        buckets.setdefault(_classify_quality(url), []).append(url)
    variants: list[VideoVariant] = []
    for rank, label in ((0, "高清"), (1, "默认"), (2, "标清")):
        group = buckets.get(rank)
        if group:
            variants.append(
                VideoVariant(label=label, rank=rank, urls=list(dict.fromkeys(group)))
            )
    return variants


def _extract_mp4_urls(html: str) -> list[str]:
    found = re.findall(MEDIA_URL_RE, html)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in found:
        if not raw.lower().endswith((".mp4",)) and ".mp4" not in raw:
            continue
        url = unescape(raw).replace("\\u002F", "/").replace("\\/", "/")
        if url in seen:
            continue
        if any(host in url for host in ASSET_HOST_HINTS):
            seen.add(url)
            cleaned.append(url)
    return cleaned


def _build_atlas_image_groups(atlas: dict[str, Any]) -> list[list[str]]:
    """atlas.list 每张图 × 全部 CDN host，返回每组候选 URL 列表（首选在前）。"""
    paths = atlas.get("list") or []
    cdns = atlas.get("cdn") or []
    if not paths:
        return []
    hosts: list[str] = []
    for cdn in cdns:
        if isinstance(cdn, dict):
            cdn = cdn.get("cdn") or ""
        if isinstance(cdn, str) and cdn:
            hosts.append(cdn)
    if not hosts:
        hosts = [""]
    groups: list[list[str]] = []
    for path in paths:
        if not isinstance(path, str):
            continue
        if path.startswith("http"):
            groups.append([path])
            continue
        variants = [
            f"https://{host}{path if path.startswith('/') else '/' + path}"
            for host in hosts
            if host
        ]
        if variants:
            groups.append(variants)
    return groups


DELETED_HINTS = (
    "作品不存在",
    "已删除",
    "已被删除",
    "作者已删除",
    '"photoStatus":1',
)


def _is_single_picture(html: str, photo_type: str) -> bool:
    """是否单图帖：photoType 为 SINGLE_PICTURE，或页面里带 singlePicture:true 标记。"""
    if photo_type == "SINGLE_PICTURE":
        return True
    return re.search(r'"singlePicture"\s*:\s*true', html) is not None


def _looks_deleted(html: str) -> bool:
    """作品已被删除 / 不存在。

    注意：「已被删除」并不包含子串「已删除」（中间夹了「被」），所以两种说法都要列出来，
    否则已删除的作品会被误报成「解析失败」，在失败样本里也分不清是内容没了还是解析器坏了。
    """
    return any(hint in html for hint in DELETED_HINTS)


def _parse_html(html: str) -> ParsedWork:
    lowered = html.lower()
    if any(hint in lowered for hint in RISK_HINTS_LOWER) or any(
        hint in html for hint in RISK_HINTS_EXACT
    ):
        raise RISK_CONTROL

    photo_type = (_first_string(html, r'"photoType"\s*:\s*"([^"]+)"') or "").upper()
    caption = _first_string(html, r'"caption"\s*:\s*"((?:\\.|[^"\\])*)"') or ""
    caption = " ".join(caption.split())
    author = _first_string(html, r'"userName"\s*:\s*"((?:\\.|[^"\\])*)"') or "快手用户"
    if author in {"userName"}:
        author = "快手用户"

    atlas = _first_json_after(html, '"atlas"')
    image_groups: list[list[str]] = []
    if isinstance(atlas, dict):
        image_groups = _build_atlas_image_groups(atlas)
    image_urls = [group[0] for group in image_groups]

    main_mv = _first_json_after(html, "mainMvUrls")
    candidates = _collect_urls(main_mv)
    candidates.extend(_extract_mp4_urls(html))
    ranked = _rank_urls(list(dict.fromkeys(candidates)))
    variants = _group_variants(ranked)
    video_url = variants[0].urls[0] if variants else (ranked[0] if ranked else None)
    video_fallbacks = (variants[0].urls[1:] if variants else ranked[1:])[:5]

    cover = _pick_url(_first_json_after(html, "coverUrls"))
    if not cover:
        cover = _first_string(html, r'"coverUrl"\s*:\s*"(https:[^"]+)"')
    if not cover:
        covers = re.findall(r"""https://[^"'\\\s>]+\.jpg[^"'\\\s>]*""", html)
        for item in covers:
            url = unescape(item)
            if "upic" in url and "uhead" not in url:
                cover = url
                break

    if image_groups and (photo_type != "VIDEO") and not video_url:
        return ParsedWork(
            type="images",
            title=caption or "快手图文",
            author=author,
            cover_url=cover or image_urls[0],
            image_urls=image_urls,
            image_groups=image_groups,
        )

    if video_url:
        return ParsedWork(
            type="video",
            title=caption or "快手视频",
            author=author,
            cover_url=cover,
            video_url=video_url,
            video_fallbacks=video_fallbacks,
            variants=variants,
        )

    if image_groups:
        return ParsedWork(
            type="images",
            title=caption or "快手图文",
            author=author,
            cover_url=cover or image_urls[0],
            image_urls=image_urls,
            image_groups=image_groups,
        )

    if _looks_deleted(html):
        raise WORK_UNAVAILABLE

    # 单图帖（photoType = SINGLE_PICTURE）：图片只出现在 coverUrls 里，既没有 atlas
    # 也没有 mainMvUrls。早期只处理了「图集」与「视频」两条路径，这类帖子会直接落到
    # PARSE_FAILED —— 真实用户就是这么撞上的（2026-09-12 失败样本）。
    # 判断放在「作品不存在」之后，避免把已删除的作品误判成一张封面图。
    if _is_single_picture(html, photo_type):
        post_images = [
            url
            for url in dict.fromkeys(_collect_urls(_first_json_after(html, "coverUrls")))
            if "uhead" not in url  # 排除头像
        ]
        if post_images:
            return ParsedWork(
                type="images",
                title=caption or "快手图文",
                author=author,
                cover_url=cover or post_images[0],
                image_urls=[post_images[0]],
                image_groups=[post_images],
            )

    raise PARSE_FAILED


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=timeout)
    try:
        current = share_url
        response = await client.get(current)
        hops = 0
        while response.is_redirect and hops < 5:
            location = response.headers.get("location")
            if not location:
                raise PARSE_FAILED
            response = await client.get(
                location,
                headers={**headers, "Referer": current},
            )
            current = str(response.url)
            hops += 1
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
        raise PARSE_FAILED
    # 主页判定放在内容解析之前：短链跳转与直接粘贴主页 URL 两种输入都覆盖
    if _is_profile_url(str(response.url)):
        raise PROFILE_LINK
    html = response.text
    if not html or len(html) < 200:
        raise PARSE_FAILED
    return _parse_html(html)
