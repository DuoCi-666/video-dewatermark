from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from app import proxies
from app.errors import PARSE_FAILED, PARSE_TIMEOUT
from app.parsers.kuaishou import ParsedWork, VideoVariant

BILI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BILI_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
BILI_REFERER = "https://www.bilibili.com/"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})", re.IGNORECASE)
AV_RE = re.compile(r"(?:^|[^\w])av(\d+)", re.IGNORECASE)
OPUS_RE = re.compile(r"/opus/(\d+)")
READ_CV_RE = re.compile(r"/read/(?:cv|mobile/)?(\d+)", re.IGNORECASE)

QN_LABELS = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
}


def _headers(
    accept: str = "application/json,text/html;q=0.9,*/*;q=0.8",
    mobile: bool = False,
) -> dict[str, str]:
    return {
        "User-Agent": BILI_MOBILE_UA if mobile else BILI_UA,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": BILI_REFERER,
        "Origin": "https://www.bilibili.com",
    }


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _collapse(text: str) -> str:
    return " ".join(str(text or "").split())


def _extract_balanced_json(html: str, marker: str) -> dict | None:
    i = html.find(marker)
    if i < 0:
        return None
    start = html.find("{", i)
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
                raw = html[start : j + 1].replace("undefined", "null")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, dict) else None
    return None


def classify_bilibili(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if "/opus/" in path or "/read/" in path or "/read/cv" in path:
        return "opus"
    if BV_RE.search(url) or AV_RE.search(url) or "/video/" in path:
        return "video"
    if "b23.tv" in (urlparse(url).hostname or "").lower():
        return "short"
    return "video"


def ids_from_url(url: str) -> tuple[str | None, int | None, str | None]:
    bv = BV_RE.search(url)
    av = AV_RE.search(url)
    opus = OPUS_RE.search(url)
    cv = READ_CV_RE.search(url)
    bvid = bv.group(1) if bv else None
    aid = int(av.group(1)) if av else None
    opus_id = opus.group(1) if opus else (cv.group(1) if cv else None)
    return bvid, aid, opus_id


def _qn_label(qn: int) -> str:
    return QN_LABELS.get(qn, f"{qn}P")


def _durl_urls(entry: dict) -> list[str]:
    urls: list[str] = []
    primary = entry.get("url")
    if isinstance(primary, str) and primary:
        urls.append(primary)
    backups = entry.get("backup_url") or []
    if isinstance(backups, list):
        urls.extend(u for u in backups if isinstance(u, str) and u)
    return list(dict.fromkeys(urls))


def parse_view_payload(payload: dict, play_by_qn: dict[int, dict]) -> ParsedWork:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise PARSE_FAILED
    title = _collapse(data.get("title") or "") or "B站视频"
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    author = _collapse(owner.get("name") or "") or "B站用户"
    cover = data.get("pic")
    cover_url = _https(cover) if isinstance(cover, str) and cover else None

    variants: list[VideoVariant] = []
    for qn in sorted(play_by_qn.keys(), reverse=True):
        play = play_by_qn[qn]
        durl = play.get("durl") if isinstance(play, dict) else None
        if not isinstance(durl, list) or not durl:
            continue
        urls = _durl_urls(durl[0] if isinstance(durl[0], dict) else {})
        if not urls:
            continue
        quality = play.get("quality") if isinstance(play.get("quality"), int) else qn
        variants.append(VideoVariant(label=_qn_label(quality), rank=-quality, urls=urls))

    if not variants:
        raise PARSE_FAILED
    return ParsedWork(
        type="video",
        title=title,
        author=author,
        cover_url=cover_url,
        video_url=variants[0].urls[0],
        video_fallbacks=variants[0].urls[1:5],
        variants=variants,
    )


def parse_opus_state(state: dict) -> ParsedWork:
    detail = ((state.get("opus") or {}).get("detail") or {}) if isinstance(state, dict) else {}
    modules = detail.get("modules") if isinstance(detail, dict) else None
    if not isinstance(modules, list):
        raise PARSE_FAILED

    title = ""
    author = ""
    groups: list[list[str]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        kind = module.get("module_type")
        if kind == "MODULE_TYPE_TITLE":
            node = module.get("module_title") or {}
            title = _collapse(node.get("text") or "")
        elif kind == "MODULE_TYPE_AUTHOR":
            node = module.get("module_author") or {}
            author = _collapse(node.get("name") or "")
        elif kind == "MODULE_TYPE_CONTENT":
            paragraphs = ((module.get("module_content") or {}).get("paragraphs")) or []
            for para in paragraphs:
                if not isinstance(para, dict):
                    continue
                pics = ((para.get("pic") or {}).get("pics")) or []
                for pic in pics:
                    if not isinstance(pic, dict):
                        continue
                    url = pic.get("url")
                    if isinstance(url, str) and url.startswith("http"):
                        groups.append([_https(url)])

    if not groups:
        raise PARSE_FAILED
    return ParsedWork(
        type="images",
        title=title or "B站图文",
        author=author or "B站用户",
        cover_url=groups[0][0],
        image_urls=[g[0] for g in groups],
        image_groups=groups,
    )


async def _json_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    response = await client.get(url, params=params, headers=_headers(), follow_redirects=True)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise PARSE_FAILED from exc
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0", None):
        raise PARSE_FAILED
    return payload


async def _resolve_share(client: httpx.AsyncClient, share_url: str) -> str:
    response = await client.get(
        share_url,
        headers=_headers("text/html,application/xhtml+xml,*/*;q=0.8", mobile=True),
        follow_redirects=True,
    )
    return str(response.url)


async def _parse_video(client: httpx.AsyncClient, url: str) -> ParsedWork:
    bvid, aid, _ = ids_from_url(url)
    params: dict[str, str | int] = {}
    if bvid:
        params["bvid"] = bvid
    elif aid:
        params["aid"] = aid
    else:
        raise PARSE_FAILED
    view = await _json_get(client, VIEW_API, params)
    data = view.get("data") if isinstance(view.get("data"), dict) else {}
    cid = data.get("cid")
    if not isinstance(cid, int):
        raise PARSE_FAILED
    accept = []
    play_by_qn: dict[int, dict] = {}
    first = await _json_get(
        client,
        PLAYURL_API,
        {"bvid": data.get("bvid") or bvid or "", "cid": cid, "qn": 64, "fnval": 1},
    )
    play = first.get("data") if isinstance(first.get("data"), dict) else {}
    if not play:
        raise PARSE_FAILED
    quality = play.get("quality")
    if isinstance(quality, int):
        play_by_qn[quality] = play
    raw_accept = play.get("accept_quality") or []
    if isinstance(raw_accept, list):
        accept = [q for q in raw_accept if isinstance(q, int)]
    for qn in accept:
        if qn in play_by_qn:
            continue
        extra = await _json_get(
            client,
            PLAYURL_API,
            {"bvid": data.get("bvid") or bvid or "", "cid": cid, "qn": qn, "fnval": 1},
        )
        extra_play = extra.get("data") if isinstance(extra.get("data"), dict) else {}
        extra_qn = extra_play.get("quality") if isinstance(extra_play, dict) else None
        if isinstance(extra_qn, int) and extra_qn not in play_by_qn:
            play_by_qn[extra_qn] = extra_play
    return parse_view_payload(view, play_by_qn)


async def _parse_opus(client: httpx.AsyncClient, url: str) -> ParsedWork:
    page = await client.get(
        url,
        headers=_headers("text/html,application/xhtml+xml,*/*;q=0.8", mobile=True),
        follow_redirects=True,
    )
    state = _extract_balanced_json(page.text, "window.__INITIAL_STATE__")
    if not state:
        raise PARSE_FAILED
    return parse_opus_state(state)


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    # B 站对海外 IP 风控（HTTP 412 / code=-412 request was banned）。
    # 配置 BILI_PROXY（如 http://<国内出口>:31280）后改用专用代理客户端出网绕开风控，
    # 不复用 main.py 的共享 PARSE_CLIENT；未配置时行为完全不变。
    # 海外部署的完整说明见 README「海外部署：地域风控与出口代理」。
    proxy = proxies.proxy_for("bilibili")
    if proxy:
        client = httpx.AsyncClient(
            headers=_headers(), follow_redirects=True, timeout=timeout, proxy=proxy
        )
        own_client = True
    else:
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(headers=_headers(), follow_redirects=True, timeout=timeout)
    try:
        try:
            kind = classify_bilibili(share_url)
            resolved = share_url
            if kind == "short" or not any(ids_from_url(share_url)):
                resolved = await _resolve_share(client, share_url)
                kind = classify_bilibili(resolved)
            if kind == "opus":
                return await _parse_opus(client, resolved)
            return await _parse_video(client, resolved)
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()
