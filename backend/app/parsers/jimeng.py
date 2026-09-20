"""即梦 AI（jimeng.jianying.com）分享链接解析。

链路（2026-09-16 实测）
-----------------------
1. 分享短链 ``https://jimeng.jianying.com/s/<token>/?t=<effect_type>`` 是 302，
   ``Location`` 指向 ``/activities/reflux/mproject?...&id=<published_item_id>&...``；
   ``id`` 即 published_item_id（等同 effect_id / item_id）。
2. ``POST /mweb/v1/get_item_info``（JSON：published_item_id + pack_item_opt）
   返回 ``data.video.origin_video.video_url`` —— 原视频直链。

水印说明（决定接入文案）
------------------------
``origin_video.logo_type = display_watermark_ending`` 表示**仅片尾烧录品牌角标**
（中段画面干净），且该标记是展示层参数：实测改写/删除 ``lr`` 参数后字节数与
MD5 完全一致，说明水印已烧进视频编码，改参数去不掉。故站点对即梦的文案应落在
「提取原视频」而非「已去水印」。

直链防盗链
----------
``v3-dreamnia.jimeng.com`` 校验 Referer：**外域 Referer 403**，无 Referer 或站内
Referer 均 206。默认（共享 client 不带 Referer）即可取用；另在 main 回源头为
jimeng CDN 补站内 Referer 作为兜底（见 ``_upstream_headers``）。

接口返回契约
------------
``ret`` 为字符串：``"0"`` 成功；``"2032"`` itemId 不存在；``"1000"`` 参数非法。
失败时 ``data`` 为 ``null``。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, RISK_CONTROL, WORK_UNAVAILABLE
from app.parsers.kuaishou import ParsedWork, VideoVariant

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

ITEM_INFO_API = "https://jimeng.jianying.com/mweb/v1/get_item_info"
ORIGIN = "https://jimeng.jianying.com"

# 已跳转的长链里直接带 id=<published_item_id>
_MWEB_ID_RE = re.compile(r"[?&]id=(\d+)")

RISK_HINTS = ("验证", "风控", "captcha", "risk", "频繁")

DEFAULT_TITLE = "即梦作品"
DEFAULT_AUTHOR = "即梦AI"

# 业务错误码：作品维度不可用
UNAVAILABLE_CODES = {"2032", "2033"}


def _item_id_from(url: str) -> str | None:
    """从长分享链接取 published_item_id（短链需先请求取 302 Location）。"""
    match = _MWEB_ID_RE.search(url or "")
    return match.group(1) if match else None


def _pid_from_location(location: str) -> str | None:
    if not location:
        return None
    values = parse_qs(urlparse(location).query).get("id") or []
    return values[0] if values and values[0].isdigit() else None


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _is_risk(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in RISK_HINTS)


def _author_name(data: dict) -> str:
    author = data.get("author")
    if isinstance(author, dict):
        name = _collapse(author.get("name"))
        if name:
            return name
    return DEFAULT_AUTHOR


def _title_of(data: dict) -> str:
    """标题兜底链：title → description → 默认值。

    实测多数作品 ``title`` 为空串，真实文案在 ``common_attr.description``。
    """
    common = data.get("common_attr") if isinstance(data.get("common_attr"), dict) else {}
    candidates = (
        data.get("title"),
        common.get("title"),
        data.get("description"),
        common.get("description"),
    )
    for candidate in candidates:
        value = _collapse(candidate)
        if value:
            return value
    return DEFAULT_TITLE


def _cover_of(data: dict) -> str | None:
    video = data.get("video") if isinstance(data.get("video"), dict) else {}
    common = data.get("common_attr") if isinstance(data.get("common_attr"), dict) else {}
    for candidate in (video.get("cover_url"), video.get("thumb"), common.get("cover_url")):
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate
    return None


def extract_work(data: dict) -> ParsedWork:
    """把 get_item_info 的 ``data`` 节点转成统一的 ParsedWork。"""
    if not isinstance(data, dict) or not data:
        raise PARSE_FAILED

    video = data.get("video") if isinstance(data.get("video"), dict) else {}
    origin = video.get("origin_video") if isinstance(video.get("origin_video"), dict) else {}
    url = origin.get("video_url")
    if not isinstance(url, str) or not url.startswith("http"):
        # 有作品但无视频直链：转码中 / 非视频类作品
        raise WORK_UNAVAILABLE

    definition = _collapse(origin.get("definition")) or "原始"
    variant = VideoVariant(label=definition, rank=0, urls=[url])

    return ParsedWork(
        type="video",
        title=_title_of(data),
        author=_author_name(data),
        cover_url=_cover_of(data),
        video_url=url,
        video_fallbacks=[],
        variants=[variant],
    )


async def parse(
    share_url: str,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*;q=0.8"},
            follow_redirects=False,
            timeout=timeout,
        )
    try:
        item_id = _item_id_from(share_url)
        if item_id is None:
            # 短链：请求一次拿 302 Location 里的 id
            try:
                response = await client.get(
                    share_url,
                    headers={
                        "User-Agent": UA,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                )
            except httpx.TimeoutException as exc:
                raise PARSE_TIMEOUT from exc
            except httpx.HTTPError as exc:
                raise PARSE_FAILED from exc
            item_id = _pid_from_location(response.headers.get("location") or "")
            if item_id is None:
                if _is_risk(response.text):
                    raise RISK_CONTROL
                raise PARSE_FAILED

        try:
            info = await client.post(
                ITEM_INFO_API,
                json={"published_item_id": item_id, "pack_item_opt": {"need_follow_info": True}},
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json,text/plain,*/*;q=0.8",
                    "Content-Type": "application/json",
                    "Origin": ORIGIN,
                    "Referer": ORIGIN + "/",
                },
            )
        except httpx.TimeoutException as exc:
            raise PARSE_TIMEOUT from exc
        except httpx.HTTPError as exc:
            raise PARSE_FAILED from exc
    finally:
        if own_client:
            await client.aclose()

    if info.status_code in {404, 410}:
        raise WORK_UNAVAILABLE
    if info.status_code >= 400:
        if _is_risk(info.text):
            raise RISK_CONTROL
        raise PARSE_FAILED

    try:
        payload = info.json()
    except ValueError:
        if _is_risk(info.text):
            raise RISK_CONTROL
        raise PARSE_FAILED from None

    if not isinstance(payload, dict):
        raise PARSE_FAILED

    ret = str(payload.get("ret")) if payload.get("ret") is not None else ""
    message = _collapse(payload.get("errmsg") or payload.get("message"))
    if ret and ret != "0":
        if ret in UNAVAILABLE_CODES or any(word in message for word in ("not exist", "不存在", "删除", "下架")):
            raise WORK_UNAVAILABLE
        if _is_risk(message):
            raise RISK_CONTROL
        raise PARSE_FAILED
    if message and _is_risk(message):
        raise RISK_CONTROL

    data = payload.get("data")
    if not isinstance(data, dict):
        raise PARSE_FAILED
    return extract_work(data)
