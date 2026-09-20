"""皮皮搞笑（h5.ippzone.com）分享链接解析。

平台辨识
--------
皮皮搞笑是最右（zuiyou）旗下的搞笑社区，H5 分享域为 ``h5.ippzone.com``，
App 包名 ``cn.xiaochuankeji.zuiyouLite``（页面 deviceInfo 里 ``h_app=zuiyou_lite``）。
注意：与另一个平台「**皮皮虾**」（``pipix.com``）**不是同一个站**，勿混淆。

链路（2026-09 实测）
--------------------
1. 分享链形如 ``https://h5.ippzone.com/spacey/post/<pid>?zy_to=copy_link&...``，
   页面本身是纯前端壳（无内嵌数据），作品数据走接口。
2. 接口：``POST https://api.ippzone.com/share/fetch_content``，
   ``Content-Type: text/plain``，请求体是 JSON 字符串：

   ::

       {"h_app":"zuiyou_lite","h_ts":<ms>,"h_ch":"","h_model":"default",
        "ua":"<UA>","h_dt":1,"in_app":false,"pid":<pid>,"type":"post"}

   成功返回 ``{"ret":1,"data":{"post":{...},"member":{...},...}}``。
   页面也可经 ``h5.ippzone.com/spacey/api/proxy?url=<api>`` 代理访问，但直连可用。

媒体结构
--------
- **视频**：``post.videos``（dict，key 为视频 id），每个含
  ``qualities[].urls[].url``（多清晰度、多 CDN）；主直链另有 ``url`` / ``urlsrc``。
  封面取 ``cover_urls[]``。
- **图集**：``post.imgs[].urls.origin.urls[]``（``/sz/src`` 原图，多 CDN）。
  注意 ``imgs`` 在视频帖里也有一条（是视频首帧），故判断顺序为「有 ``videos`` 即视频，
  否则用 ``imgs`` 作图集」。

作者
----
作者昵称在 ``data.member.name``（服务端返回偶有缺省），取不到时回退默认值。

防盗链
------
图片 CDN（``*.ippzone.com``）与视频 CDN（``*.szsttkj.com``）实测**不校验 Referer**，
共享 client 直接可取；均支持 Range（206）。直链未带短时效签名，故不注册 signed_media。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

API_URL = "https://api.ippzone.com/share/fetch_content"
ORIGIN = "https://h5.ippzone.com"

SHARE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "text/plain",
    "Origin": ORIGIN,
    "Referer": ORIGIN + "/",
}

# /spacey/post/<pid>  或  ?pid=<pid>  或 ?id=<pid>
_PID_PATH_RE = re.compile(r"/post/(\d+)")
_PID_QUERY_RE = re.compile(r"[?&]pid=(\d+)")
_SHARE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "text/plain",
    "Origin": ORIGIN,
    "Referer": ORIGIN + "/",
}

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁", "安全")

DEFAULT_TITLE = "皮皮搞笑作品"
DEFAULT_AUTHOR = "皮皮搞笑"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _is_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _pid_from(url: str) -> str | None:
    match = _PID_PATH_RE.search(url or "")
    if match:
        return match.group(1)
    match = _PID_QUERY_RE.search(url or "")
    return match.group(1) if match else None


def _req_body(pid: str) -> str:
    payload = {
        "h_app": "zuiyou_lite",
        "h_ts": int(time.time() * 1000),
        "h_ch": "",
        "h_model": "default",
        "ua": UA,
        "h_dt": 1,
        "in_app": False,
        "pid": int(pid) if str(pid).isdigit() else pid,
        "type": "post",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _collect_urls(urls: Any) -> list[str]:
    """从 [{"url": ...}] 或 ["url", ...] 里收集 http 直链（去重保序）。"""
    out: list[str] = []
    if isinstance(urls, str):
        urls = [urls]
    if isinstance(urls, list):
        for entry in urls:
            url = entry.get("url") if isinstance(entry, dict) else entry
            if isinstance(url, str) and url.startswith("http") and url not in out:
                out.append(url)
    return out


def _video_variants(post: dict) -> list[VideoVariant]:
    videos = post.get("videos")
    if not isinstance(videos, dict) or not videos:
        return []
    variants: list[VideoVariant] = []
    seen: set[str] = set()
    for video in videos.values():
        if not isinstance(video, dict):
            continue
        # 优先用 qualities（按清晰度分档、每档多 CDN）；无则退回 url/urlsrc
        qualities = video.get("qualities")
        if isinstance(qualities, list) and qualities:
            for quality in qualities:
                if not isinstance(quality, dict):
                    continue
                urls = [u for u in _collect_urls(quality.get("urls")) if u not in seen]
                if not urls:
                    continue
                seen.update(urls)
                label = str(quality.get("resolution") or "").strip()
                variants.append(
                    VideoVariant(label=label or "默认", rank=len(variants), urls=urls)
                )
        else:
            fallback = [video.get("url"), video.get("urlsrc"), video.get("urlext")]
            urls = [u for u in _collect_urls(fallback) if u not in seen]
            if urls:
                seen.update(urls)
                variants.append(VideoVariant(label="默认", rank=len(variants), urls=urls))
    return variants


def _cover_of(post: dict) -> str | None:
    videos = post.get("videos")
    if isinstance(videos, dict):
        for video in videos.values():
            if isinstance(video, dict):
                urls = _collect_urls(video.get("cover_urls"))
                if urls:
                    return urls[0]
    imgs = post.get("imgs")
    if isinstance(imgs, list) and imgs and isinstance(imgs[0], dict):
        urls = _collect_urls((imgs[0].get("urls") or {}).get("540", {}).get("urls"))
        if urls:
            return urls[0]
    return None


def _image_groups(post: dict) -> list[list[str]]:
    imgs = post.get("imgs")
    if not isinstance(imgs, list):
        return []
    groups: list[list[str]] = []
    for img in imgs:
        if not isinstance(img, dict):
            continue
        urls_map = img.get("urls") if isinstance(img.get("urls"), dict) else {}
        # origin 为原图（/sz/src），回退到 540
        urls = _collect_urls((urls_map.get("origin") or {}).get("urls"))
        if not urls:
            urls = _collect_urls((urls_map.get("540") or {}).get("urls"))
        if urls:
            groups.append(urls)
    return groups


def extract_work(data: dict) -> ParsedWork:
    """把 fetch_content 返回的 ``data`` 转成统一的 ParsedWork。"""
    if not isinstance(data, dict):
        raise PARSE_FAILED
    post = data.get("post")
    if not isinstance(post, dict) or not post:
        raise WORK_UNAVAILABLE

    member = data.get("member") if isinstance(data.get("member"), dict) else {}
    author = _collapse(member.get("name")) or DEFAULT_AUTHOR
    title = _collapse(post.get("content")) or DEFAULT_TITLE
    cover = _cover_of(post)

    variants = _video_variants(post)
    if variants:
        return ParsedWork(
            type="video",
            title=title,
            author=author,
            cover_url=cover,
            video_url=variants[0].urls[0],
            video_fallbacks=variants[0].urls[1:],
            variants=variants,
        )

    groups = _image_groups(post)
    if groups:
        return ParsedWork(
            type="images",
            title=title,
            author=author,
            cover_url=cover,
            image_urls=[group[0] for group in groups],
            image_groups=groups,
        )

    raise WORK_UNAVAILABLE


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    pid = _pid_from(share_url)
    if pid is None:
        raise PARSE_FAILED

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=SHARE_HEADERS, timeout=timeout)
    try:
        try:
            response = await client.post(
                API_URL, content=_req_body(pid), headers=SHARE_HEADERS
            )
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
        if _is_risk(response.text):
            raise RISK_CONTROL
        raise PARSE_FAILED

    try:
        payload = response.json()
    except ValueError:
        if _is_risk(response.text):
            raise RISK_CONTROL
        raise PARSE_FAILED from None

    if not isinstance(payload, dict):
        raise PARSE_FAILED
    ret = payload.get("ret")
    if ret not in (1, "1"):
        message = _collapse(payload.get("msg"))
        if _is_risk(message):
            raise RISK_CONTROL
        # ret=-1 且提示作品不存在 / 已删除 → 作品不可用
        if any(word in message for word in ("不存在", "删除", "下架", "not exist")):
            raise WORK_UNAVAILABLE
        raise PARSE_FAILED
    data = payload.get("data")
    if not isinstance(data, dict):
        raise WORK_UNAVAILABLE
    return extract_work(data)
