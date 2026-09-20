"""解析编排：链接 → 平台 → 解析器 → 前端 payload。

分层：
- extract_share_url 负责「这串文本属于哪个平台、分享链接是什么」；
- 各平台 parse_xxx 负责「拿到分享页/接口数据、抽出媒体直链」（app/parsers/）；
- 本模块负责串起来：缓存、解析器选择、失败记账、payload 组装、token 签发、直链刷新。

批量解析与单条解析共用 _parse_one / _build_payload，保证两者结构一致。
"""
from __future__ import annotations

import importlib
import time
import httpx

from app import failures, samples, stats, tokens
from app.clients import get_parse_client
from app.errors import EMPTY_INPUT, PARSE_TIMEOUT, ParseError
from app.parsers.naming import filename_from_title
from app.parsers import extract
from app.parsers.extract import (
    SIGNED_MEDIA_PLATFORMS,
    extract_first_url,
    extract_share_url,
)

# 平台 key -> parsers 子模块名的历史差异表。
# 目前只有小红书（key=xiaohongshu，模块=xhs）。新平台请保持同名，别再加别名。
_MODULE_ALIASES: dict[str, str] = {"xiaohongshu": "xhs"}

# 解析结果缓存（按分享链接去重）：同一链接短时间内重复解析直接复用结果。
# 缓存的是已签发 token 的 payload —— token 有独立 TTL，过期后刷新逻辑仍可用。
CACHE_TTL_MS = 5 * 60 * 1000
_CACHE: dict[str, tuple[int, dict]] = {}


def _parser_module_name(platform: str) -> str:
    """平台 key -> parsers 子模块名。

    约定默认同名（`douyin` -> `app.parsers.douyin`）。历史遗留的少数差异在
    `_MODULE_ALIASES` 里显式登记 —— 新平台请保持同名，不要新增别名。
    """
    return _MODULE_ALIASES.get(platform, platform)


def _load_parser(platform: str):
    """按平台 key 解析出解析函数。

    两种来源，按优先级：
    1. `app.main.parse_<key>`（历史契约）—— 测试与部署者会 monkeypatch 这里，
       所以优先读它，保证补丁生效；
    2. 回退到 `app.parsers.<module>.parse` —— 让新平台不必再改 main.py。
    """
    from app import main

    fn = getattr(main, f"parse_{platform}", None)
    if fn is not None:
        return fn
    module = importlib.import_module(f"app.parsers.{_parser_module_name(platform)}")
    return module.parse


def _parsers() -> dict:
    """平台 key -> 解析函数，由 extract.PLATFORMS 自动推导。

    刻意做成函数而不是模块级字典：每次调用时重新解析，测试里 monkeypatch
    `app.main.parse_xxx` 才能生效（这是既有测试依赖的契约）。

    刻意从 PLATFORMS 推导而不是手写映射：历史上手写映射漏过 weibo / jimeng /
    pipix 三个平台，导致「平台能识别、清单一览可见，一解析就 KeyError」。
    现在新增平台只需在 PLATFORMS 加一行 + 建 parsers/<key>.py。
    一致性由 tests/test_platform_registry.py 守着。
    """
    # 经模块属性读取，保证 PLATFORMS 是唯一真源（而非本模块的副本）
    return {item.key: _load_parser(item.key) for item in extract.PLATFORMS}


def cache_size() -> int:
    """当前缓存的解析结果条数（供 /api/health 观测）。"""
    return len(_CACHE)


def _cache_get(key: str) -> dict | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    expires_at, payload = entry
    if expires_at < int(time.time() * 1000):
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict) -> None:
    now = int(time.time() * 1000)
    _CACHE[key] = (now + CACHE_TTL_MS, payload)
    for stale in [k for k, (exp, _) in _CACHE.items() if exp < now]:
        _CACHE.pop(stale, None)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _refresh_info(platform: str, share_url: str, kind: str, index: int = 0) -> dict | None:
    """只有短时效签名平台才带刷新描述，其余平台保持 None（行为不变）。"""
    if platform not in SIGNED_MEDIA_PLATFORMS:
        return None
    return {"platform": platform, "share_url": share_url, "kind": kind, "index": index}


def _urls_for_refresh(work, info: dict) -> list[str]:
    kind = info.get("kind")
    if kind == "video":
        return [url for url in [work.video_url, *(work.video_fallbacks or [])] if url]
    if kind == "cover":
        return [work.cover_url] if work.cover_url else []
    if kind == "image":
        groups = work.image_groups or [[url] for url in work.image_urls]
        index = info.get("index") or 0
        if 0 <= index < len(groups):
            return list(groups[index])
    return []


def _build_payload(work, platform: str, share_url: str) -> dict:
    """把解析结果组装成前端消费的 payload，并签发媒体 token。

    单条解析与批量解析共用，保证两者返回结构一致。
    """
    safe_title = work.title or "kuaishou"
    payload: dict = {
        "ok": True,
        "type": work.type,
        "title": work.title,
        "author": work.author,
        "coverUrl": None,
        "videoUrl": None,
        "downloadUrl": None,
        "images": [],
    }
    if work.cover_url:
        token = tokens.issue_multi(
            [work.cover_url],
            "cover",
            filename_from_title(safe_title, "jpg", fallback="cover"),
            refresh=_refresh_info(platform, share_url, "cover"),
        )
        payload["coverUrl"] = f"/api/media/{token}"
    if work.type == "video" and work.video_url:
        variants_out = []
        if work.variants:
            for variant in work.variants:
                token = tokens.issue_multi(
                    variant.urls,
                    "video",
                    filename_from_title(safe_title, "mp4"),
                    refresh=_refresh_info(platform, share_url, "video"),
                )
                variants_out.append(
                    {
                        "label": variant.label,
                        "mediaUrl": f"/api/media/{token}",
                        "downloadUrl": f"/api/download/{token}",
                    }
                )
        else:
            fallback = [work.video_url] + [
                u for u in work.video_fallbacks if u != work.video_url
            ]
            token = tokens.issue_multi(
                fallback[:5],
                "video",
                filename_from_title(safe_title, "mp4"),
                refresh=_refresh_info(platform, share_url, "video"),
            )
            variants_out.append(
                {
                    "label": "默认",
                    "mediaUrl": f"/api/media/{token}",
                    "downloadUrl": f"/api/download/{token}",
                }
            )
        payload["variants"] = variants_out
        payload["videoUrl"] = variants_out[0]["mediaUrl"]
        payload["downloadUrl"] = variants_out[0]["downloadUrl"]
    if work.type == "images":
        groups = work.image_groups or [[u] for u in work.image_urls]
        images = []
        for index, variants in enumerate(groups, start=1):
            url = variants[0]
            ext = "jpg"
            lower = url.lower()
            for candidate in ("png", "webp", "jpeg", "jpg"):
                if f".{candidate}" in lower:
                    ext = "jpg" if candidate == "jpeg" else candidate
                    break
            token = tokens.issue_multi(
                variants,
                "image",
                filename_from_title(f"{safe_title}_{index}", ext, fallback="image"),
                refresh=_refresh_info(platform, share_url, "image", index - 1),
            )
            images.append(
                {
                    "previewUrl": f"/api/media/{token}",
                    "downloadUrl": f"/api/download/{token}",
                }
            )
        payload["images"] = images
    return payload


def _safe_first_url(text: str) -> str:
    """尽量把链接摘出来。

    平台不被支持时链接本身仍是最关键的信息（能直接拿去复现、或成为接入新平台的
    起点），所以这里单独再摘一次，而不是记成空。
    """
    try:
        return extract_first_url(text)
    except ParseError:
        return ""


def _record_failure(
    *,
    platform: str,
    url: str,
    text: str,
    code: str,
    message: str,
    ms: int,
    source: str,
    client_ip: str,
) -> None:
    """失败时同时落两项：计数（统计）与样本（失败流水，供事后复现）。"""
    stats.STATS.record(platform, ok=False, ms=ms, error=code, source=source)
    failures.SAMPLES.record(
        url=url,
        input_text=text,
        platform=platform,
        code=code,
        message=message,
        ms=ms,
        source=source,
        client_ip=client_ip,
    )


async def parse_one(text: str, source: str, client_ip: str) -> dict:
    """解析单条链接并组装结果，供 /api/parse 与批量解析共用。

    成功返回 payload；失败时已记录统计与失败样本，然后抛出对应 ParseError，
    由调用方决定是直接响应还是标记为批量中的单条失败。
    """
    started = time.monotonic()
    if not (text or "").strip():
        raise EMPTY_INPUT
    try:
        platform, share_url = extract_share_url(text)
    except ParseError as exc:
        _record_failure(
            platform=stats.REJECTED,
            url=_safe_first_url(text),
            text=text,
            code=exc.code,
            message=exc.message,
            ms=_elapsed_ms(started),
            source=source,
            client_ip=client_ip,
        )
        raise
    cached = _cache_get(share_url)
    if cached is not None:
        stats.STATS.record(
            platform, ok=True, ms=_elapsed_ms(started), cached=True, source=source
        )
        # 缓存命中同样证明这条链接可用：累加命中次数，让「反复成功」的链接
        # 在候选清单里置信度更高（配合 candidates 的 min_count 过滤）。
        samples.SAMPLES.record(
            platform=platform, url=share_url, kind=(cached.get("type") or ""), source=source
        )
        return cached
    try:
        parse_fn = _parsers()[platform]
        work = await parse_fn(share_url, timeout=15.0, client=get_parse_client())
    except httpx.TimeoutException as exc:
        _record_failure(
            platform=platform,
            url=share_url,
            text=text,
            code=PARSE_TIMEOUT.code,
            message=PARSE_TIMEOUT.message,
            ms=_elapsed_ms(started),
            source=source,
            client_ip=client_ip,
        )
        raise PARSE_TIMEOUT from exc
    except ParseError as exc:
        _record_failure(
            platform=platform,
            url=share_url,
            text=text,
            code=exc.code,
            message=exc.message,
            ms=_elapsed_ms(started),
            source=source,
            client_ip=client_ip,
        )
        raise
    except Exception:
        _record_failure(
            platform=platform,
            url=share_url,
            text=text,
            code="INTERNAL_ERROR",
            message="服务器内部错误，请稍后重试",
            ms=_elapsed_ms(started),
            source=source,
            client_ip=client_ip,
        )
        raise

    payload = _build_payload(work, platform, share_url)
    stats.STATS.record(platform, ok=True, ms=_elapsed_ms(started), source=source)
    # 之前失败过的链接现在能解析了：从失败待办里移除，闭环收口
    failures.SAMPLES.resolve(share_url, platform=platform)
    # 把「确实能用」的链接沉淀成巡检候选，省去手工收集样本（可 SAMPLES_ENABLE=0 关）
    samples.SAMPLES.record(
        platform=platform, url=share_url, kind=work.type or "", source=source
    )
    _cache_put(share_url, payload)
    return payload


async def refresh_media_urls(item) -> list[str] | None:
    """媒体直链签名过期后重新解析，原地换掉该 token 的候选（token 本身不变，
    所以前端已发出的 /api/media/<token> 无需重发即可恢复）。"""
    info = item.refresh
    if not isinstance(info, dict):
        return None
    parse_fn = _parsers().get(info.get("platform"))
    share_url = info.get("share_url")
    if parse_fn is None or not share_url:
        return None
    try:
        work = await parse_fn(share_url, timeout=15.0, client=get_parse_client())
    except Exception:  # noqa: BLE001 - 刷新失败按原样报下载失败，不再向上抛
        return None
    urls = _urls_for_refresh(work, info)
    if not urls:
        return None
    tokens.update_sources(item.token, urls)
    return urls
