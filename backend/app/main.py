from __future__ import annotations

import asyncio
import sys
import types
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from pydantic import BaseModel, Field

from app import batch, failures, clients, guard, pack, parsing, samples, stats, tokens
from app.errors import EMPTY_INPUT, INTERNAL_ERROR, NOT_FOUND, ParseError
# 以下 media 符号在本模块仅作兼容再导出：解析编排与媒体代理已分别落在
# app.parsing / app.media，但历史测试与外部脚本仍以 app.main.xxx 路径引用。
from app.media import (  # noqa: F401  (兼容再导出)
    FFMPEG_AVAILABLE,
    _hls_response,
    _to_mp4_name,
    looks_like_hls as _looks_like_hls,
    proxy as _proxy,
)
from app.parsers.extract import (
    URL_RE,
    platform_list,
    split_link_segments,
)
from app.parsers.naming import filename_from_title  # noqa: F401  (测试/插件兼容出口)
# 同理：这些解析器以 app.main.parse_xxx 暴露，_parsers() 每次调用时动态读取，
# 因此测试里 monkeypatch app.main.parse_xxx 才能生效。
from app.parsers.kuaishou import parse as parse_kuaishou  # noqa: F401
from app.parsers.douyin import parse as parse_douyin  # noqa: F401
from app.parsers.xhs import parse as parse_xiaohongshu  # noqa: F401

# 兼容别名：历史上小红书解析器叫 parse_xhs，部分测试仍 patch 这个名字。
parse_xhs = parse_xiaohongshu
from app.parsers.doubao import parse as parse_doubao  # noqa: F401
from app.parsers.bilibili import parse as parse_bilibili  # noqa: F401
from app.parsers.qqchannel import parse as parse_qqchannel  # noqa: F401
from app.parsers.miyoushe import parse as parse_miyoushe  # noqa: F401
from app.parsers.weibo import parse as parse_weibo  # noqa: F401
from app.parsers.jimeng import parse as parse_jimeng  # noqa: F401
from app.parsers.pipix import parse as parse_pipix  # noqa: F401
from app.parsers.pipigaoxiao import parse as parse_pipigaoxiao  # noqa: F401
from app.parsers.zuiyou import parse as parse_zuiyou  # noqa: F401
from app.parsers.weishi import parse as parse_weishi  # noqa: F401
from app.parsers.toutiao import parse as parse_toutiao  # noqa: F401
from app.parsers.cctv import parse as parse_cctv  # noqa: F401
from app.parsers.acfun import parse as parse_acfun  # noqa: F401
from app.parsers.qqvideo import parse as parse_qqvideo  # noqa: F401
from app.parsers.sohu import parse as parse_sohu  # noqa: F401
from app.parsers.lishipin import parse as parse_lishipin  # noqa: F401
from app.parsers.huya import parse as parse_huya  # noqa: F401
from app.parsers.zhihu import parse as parse_zhihu  # noqa: F401
from app.parsers.haokan import parse as parse_haokan  # noqa: F401

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

PARSE_TIMEOUT = httpx.Timeout(15.0)
CLIENT_LIMITS = httpx.Limits(
    max_keepalive_connections=20,
    max_connections=100,
    keepalive_expiry=120.0,
)


# —— 兼容层：共享客户端 ——
# 历史上 PARSE_CLIENT / PROXY_CLIENT 是 main 的模块级全局，测试与外部脚本会用
# `monkeypatch.setattr("app.main.PROXY_CLIENT", ...)` 替换。拆分后真正的持有者
# 移到 app.clients，这里用模块级 __getattr__/__setattr__ 做透明转发，保持兼容。
# 拆分后这些实现搬到了 media / pack / parsing，但历史测试与外部脚本仍以
# `app.main.xxx` 的字符串路径引用它们（monkeypatch.setattr / from ... import）。
# 为此把本模块的类换成 ModuleType 子类：读不到的名字转发到子模块，
# 写入的名字（含 PARSE_CLIENT / PROXY_CLIENT）落到共享容器或对应子模块，
# 保证既有引用方式与拆分前行为一致。
_FORWARD_TO_CHILD: dict = {}
_RESERVED = frozenset(
    {"__path__", "__name__", "__file__", "__spec__", "__loader__", "__package__"}
)


def _init_forwarding() -> dict:
    """建立旧名 -> (子模块, 实际属性名) 的转发表，首次取用时才构建以避开循环导入。"""
    if _FORWARD_TO_CHILD:
        return _FORWARD_TO_CHILD
    from app import media as _media, pack as _pack, parsing as _parsing

    _FORWARD_TO_CHILD.update(
        {
            "_parse_one": (_parsing, "parse_one"),
            "_build_payload": (_parsing, "_build_payload"),
            "_cache_get": (_parsing, "_cache_get"),
            "_cache_put": (_parsing, "_cache_put"),
            "_elapsed_ms": (_parsing, "_elapsed_ms"),
            "_refresh_info": (_parsing, "_refresh_info"),
            "_urls_for_refresh": (_parsing, "_urls_for_refresh"),
            "_refresh_media": (_parsing, "refresh_media_urls"),
            "_safe_first_url": (_parsing, "_safe_first_url"),
            "_record_failure": (_parsing, "_record_failure"),
            "_parsers": (_parsing, "_parsers"),
            "_CACHE": (_parsing, "_CACHE"),
            "CACHE_TTL_MS": (_parsing, "CACHE_TTL_MS"),
            "extract_share_url": (_parsing, "extract_share_url"),
            "extract_first_url": (_parsing, "extract_first_url"),
            "SIGNED_MEDIA_PLATFORMS": (_parsing, "SIGNED_MEDIA_PLATFORMS"),
            "_open_media": (_media, "open_media"),
            "_upstream_headers": (_media, "_upstream_headers"),
            "_looks_like_hls": (_media, "looks_like_hls"),
            "_to_mp4_name": (_media, "_to_mp4_name"),
            "_ffmpeg_mp4_stream": (_media, "_ffmpeg_mp4_stream"),
            "_hls_cached": (_media, "_hls_cached"),
            "_hls_response": (_media, "_hls_response"),
            "_hls_transcode": (_media, "_hls_transcode"),
            "_hls_cache_sweep": (_media, "_hls_cache_sweep"),
            "_hls_file_task": (_media, "_hls_file_task"),
            "_proxy": (_media, "proxy"),
            "_HLS_FILES": (_media, "_HLS_FILES"),
            "_HLS_TASKS": (_media, "_HLS_TASKS"),
            "_HLS_SEM": (_media, "_HLS_SEM"),
            "HLS_CACHE_DIR": (_media, "HLS_CACHE_DIR"),
            "HLS_CACHE_TTL_SEC": (_media, "HLS_CACHE_TTL_SEC"),
            "HLS_TRANSCODE": (_media, "HLS_TRANSCODE"),
            "HLS_TRANSCODE_CONCURRENCY": (_media, "HLS_TRANSCODE_CONCURRENCY"),
            "HLS_TRANSCODE_TIMEOUT_SEC": (_media, "HLS_TRANSCODE_TIMEOUT_SEC"),
            "FFMPEG_AVAILABLE": (_media, "FFMPEG_AVAILABLE"),
            "UPSTREAM_ATTEMPTS": (_media, "UPSTREAM_ATTEMPTS"),
            "UPSTREAM_RETRY_INTERVAL": (_media, "UPSTREAM_RETRY_INTERVAL"),
            "_PACKS": (_pack, "_PACKS"),
            "_PACK_ORDER": (_pack, "_PACK_ORDER"),
            "_PACK_SEM": (_pack, "_PACK_SEM"),
            "_run_pack": (_pack, "run_pack"),
            "_pack_sweep": (_pack, "_pack_sweep"),
            "_get_pack": (_pack, "get_pack"),
            "_pack_now_iso": (_pack, "_pack_now_iso"),
            "_collect_media": (_pack, "collect_media"),
            "_filename_safe": (_pack, "_filename_safe"),
            "_media_token": (_pack, "_media_token"),
            "PackState": (_pack, "PackState"),
            "pack_dir": (_pack, "pack_dir"),
            "PACK_TTL_SEC": (_pack, "PACK_TTL_SEC"),
            "PACK_CONCURRENCY": (_pack, "PACK_CONCURRENCY"),
            "ZIP_ITEM_MAX": (_pack, "ZIP_ITEM_MAX"),
            "ZIP_ARCHIVE_MAX": (_pack, "ZIP_ARCHIVE_MAX"),
        }
    )
    return _FORWARD_TO_CHILD


class _MainModule(types.ModuleType):
    """main 模块自身：把旧私有名透明转发到拆分后的子模块。

    - 读：先查本模块真实定义；没有再按转发表取子模块属性；
    - 写：PARSE_CLIENT/PROXY_CLIENT 写进 app.clients（业务方统一从容器取），
      其余写到对应子模块，确保「改一处、全局生效」。
    """

    def __getattr__(self, name: str):
        if name == "PARSE_CLIENT":
            return clients.get_parse_client()
        if name == "PROXY_CLIENT":
            return clients.get_proxy_client()
        target = _init_forwarding().get(name)
        if target is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return getattr(target[0], target[1])

    def __setattr__(self, name: str, value) -> None:
        if name == "PARSE_CLIENT":
            clients.set_parse_client(value)
            return
        if name == "PROXY_CLIENT":
            clients.set_proxy_client(value)
            return
        if name not in _RESERVED:
            target = _init_forwarding().get(name)
            if target is not None:
                setattr(target[0], target[1], value)
                return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _MainModule

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动时建好共享 HTTP 客户端，退出时落盘统计并关闭。"""
    from app.media import PROXY_TIMEOUT

    clients.set_parse_client(
        httpx.AsyncClient(
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=False,
            timeout=PARSE_TIMEOUT,
            limits=CLIENT_LIMITS,
        )
    )
    clients.set_proxy_client(
        httpx.AsyncClient(
            headers={
                "User-Agent": UA,
                # 不带 Referer：douyinvod 等抖音 CDN 有 Referer 防盗链，外域 Referer 会 403
                "Accept": "*/*",
            },
            follow_redirects=True,
            timeout=PROXY_TIMEOUT,
            limits=CLIENT_LIMITS,
        )
    )
    try:
        yield
    finally:
        # 退出前把统计与失败样本落盘，避免进程重启丢数据
        stats.STATS.close()
        failures.SAMPLES.close()
        samples.SAMPLES.close()
        for getter in (clients.get_parse_client, clients.get_proxy_client):
            client = getter()
            if client is not None:
                await client.aclose()
        clients.set_parse_client(None)
        clients.set_proxy_client(None)


app = FastAPI(
    title="video-dewatermark 解析服务",
    description=(
        "聚合视频去水印解析工具站后端。粘贴分享链接 → 解析无水印原片 / 原图 → "
        "在线预览与下载。支持多平台（见 /api/platforms），并提供批量解析、"
        "打包下载、调用统计与失败样本诊断。"
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "元信息", "description": "服务健康与平台清单"},
        {"name": "解析", "description": "单条 / 批量解析分享链接"},
        {"name": "媒体", "description": "媒体代理转发与下载（含 HLS 转封装、打包 ZIP）"},
        {"name": "统计", "description": "调用统计与失败样本诊断"},
    ],
)
guard.register_error_handlers(app)


class ParseBody(BaseModel):
    input: str = Field(default="", max_length=2000)


@app.exception_handler(ParseError)
async def parse_error_handler(_: Request, exc: ParseError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(
        status_code=exc.status,
        content={"ok": False, "code": exc.code, "message": exc.message},
        headers=headers,
    )


@app.get("/api/health", tags=["元信息"], summary="健康检查")
async def health():
    return {
        "ok": True,
        "tokens": tokens.size(),
        "cache": parsing.cache_size(),
        "guard": guard.GUARD.stats(),
    }


@app.get("/api/platforms", tags=["元信息"], summary="支持平台清单")
async def platforms_api():
    """支持的平台清单（前端据此渲染徽标与文案，避免前后端各维护一份清单）。"""
    return JSONResponse(
        content={"ok": True, "platforms": platform_list()},
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/stats", tags=["统计"], summary="调用统计（按天 × 平台）")
async def stats_api(
    request: Request,
    fmt: str = "json",
    date: str | None = None,
    days: int | None = None,
):
    """解析调用统计（按天 × 平台的成功率 / 耗时）。

    默认只对本机开放 —— 运营数据不对外。需要远程查看请设置 STATS_TOKEN
    并带上 X-Stats-Token 头。详见 app/stats.py 的安全说明。
    """
    if not stats.is_local(request):
        raise NOT_FOUND
    report = stats.STATS.report(date=date, days=days)
    if fmt == "text":
        return PlainTextResponse(stats.render_text(report))
    return report


@app.get("/api/stats/report", tags=["统计"], summary="一键诊断报告")
async def stats_report_api(
    request: Request,
    days: int | None = None,
    limit: int = 50,
    kind: str | None = None,
):
    """一键诊断报告：把「解析统计」与「失败样本」合成一份可直接投喂给 AI 的文本。

    定位：``/api/stats`` 与 ``/api/stats/failures`` 各看一半，排查时要来回取两次、
    还要手工拼上下文；这里一次返回「现状（成功率/耗时）+ 待修样本（含原始输入）」，
    直接复制给模型即可完成「定位 → 修解析器 → 看统计回升」的闭环。

    - ``days`` 限定统计窗口（默认全部保留期）；``limit`` 限制样本条数
    - ``kind`` 只看指定定性，如 ``?kind=input,parser_bug``
    - 与另外两个接口一致：只对「未经代理直连」的本机开放
    """
    if not stats.is_local(request):
        raise NOT_FOUND
    stats_report = stats.STATS.report(days=days)
    kinds = {item.strip() for item in kind.split(",") if item.strip()} if kind else None
    failures_report = failures.SAMPLES.report(limit=limit, kinds=kinds)
    return PlainTextResponse(stats.render_health_report(stats_report, failures_report))


@app.get("/api/stats/failures", tags=["统计"], summary="失败样本流水")
async def failures_api(
    request: Request,
    fmt: str = "json",
    limit: int = 50,
    kind: str | None = None,
):
    """解析失败样本：具体哪条链接、错在哪一环（供复现与修复）。

    - `kind` 可传逗号分隔的定性，如 `?kind=input,parser_bug` 只看"值得修的"
    - `fmt=text` 输出可直接复制给 AI 的文本块
    - 与 /api/stats 一样只对本机开放
    """
    if not stats.is_local(request):
        raise NOT_FOUND
    kinds = {item.strip() for item in kind.split(",") if item.strip()} if kind else None
    report = failures.SAMPLES.report(limit=limit, kinds=kinds)
    if fmt == "text":
        return PlainTextResponse(failures.render_text(report))
    return report


@app.get("/api/stats/samples", tags=["统计"], summary="成功样本池（巡检候选）")
async def samples_api(
    request: Request,
    per_platform: int = 2,
    min_count: int = 1,
):
    """把「真实用户成功解析过的链接」按平台列出，供挑巡检样本。

    巡检样本要真实链接，但手工收集是持续负担；而每次解析成功后端都经手过一条
    确实能用的链接。这里把它们按平台归组给出，可直接复制进 ops/links.json
    或私有清单（VD_LINKS_EXTRA）。

    隐私：入库前已剥离 xsec_token / share_id 等分享凭证；数据只落本机
    backend/data/（已在 .gitignore），绝不进仓库。可用 SAMPLES_ENABLE=0 关闭收录。
    与另外几个统计接口一致，只对**未经代理直连**的本机开放。
    """
    if not stats.is_local(request):
        raise NOT_FOUND
    return {
        "ok": True,
        "report": samples.SAMPLES.report(),
        "candidates": samples.SAMPLES.candidates(
            per_platform=max(min(per_platform, 20), 1),
            min_count=max(min_count, 1),
        ),
    }


@app.get("/api/stats/samples/export", tags=["统计"], summary="导出巡检样本清单")
async def samples_export_api(
    request: Request,
    per_platform: int = 2,
    min_count: int = 1,
    fmt: str = "text",
):
    """把成功样本池导出成巡检清单格式，可直接存成 VD_LINKS_EXTRA 指向的文件。

    ``fmt=text`` 返回可直接落盘的 JSON 文本；``fmt=json`` 返回结构化结果。
    """
    if not stats.is_local(request):
        raise NOT_FOUND
    result = samples.SAMPLES.export_links(
        per_platform=max(min(per_platform, 20), 1),
        min_count=max(min_count, 1),
        fmt=fmt,
    )
    if fmt == "text":
        return PlainTextResponse(result["text"])
    return {"ok": True, "count": result["count"], "links": result["links"]}


@app.post(
    "/api/parse",
    tags=["解析"],
    summary="解析单条分享链接",
    dependencies=[Depends(guard.parse_guard)],
)
async def parse_api(body: ParseBody, request: Request):
    # 巡检脚本会带 X-VD-Source: healthcheck，单独归类，避免污染真实用户数据
    source = (request.headers.get("x-vd-source") or "web").strip()[:32] or "web"
    client_ip = guard.client_ip(request)
    text = (body.input or "").strip()
    # _parse_one 对空输入抛 EMPTY_INPUT（不记录统计，与旧行为一致）
    return await parsing.parse_one(text, source, client_ip)


class BatchBody(BaseModel):
    items: list[str] = Field(default_factory=list, max_length=batch.MAX_ITEMS)


async def _run_batch_job(job: batch.Job) -> None:
    """后台执行批量 job：有界并发逐条解析，单条失败只标记该项，不中断其他条目。"""
    try:
        for item in job.items:
            item.status = "running"
            batch.STORE.touch(job)
            started = time.monotonic()
            try:
                async with batch.SEMAPHORE:
                    payload = await parsing.parse_one(item.text, job.source, job.client_ip)
                item.ok = True
                item.result = payload
            except ParseError as exc:
                item.ok = False
                item.code = exc.code
                item.message = exc.message
            except Exception:  # _parse_one 已将异常转结构化，这里纯兜底
                item.ok = False
                item.code = INTERNAL_ERROR.code
                item.message = INTERNAL_ERROR.message
            item.ms = parsing._elapsed_ms(started)
            item.status = "done"
            job.done += 1
            if item.ok:
                job.ok_count += 1
            else:
                job.fail_count += 1
            batch.STORE.touch(job)
    finally:
        job.status = "done"
        batch.STORE.touch(job)


@app.post(
    "/api/batch",
    tags=["解析"],
    summary="提交批量解析",
    dependencies=[Depends(guard.batch_guard)],
)
async def batch_create(body: BatchBody, request: Request):
    """提交批量解析：把一组链接丢给后台任务，立即返回 job_id。

    逐条支持多行粘贴（每一项内部会再按行拆分、去空、去重），复用单条解析路径，
    全部平台自动覆盖。结果通过 GET /api/batch/{id} 查询。
    """
    source = (request.headers.get("x-vd-source") or "batch").strip()[:32] or "batch"
    client_ip = guard.client_ip(request)

    items: list[str] = []
    seen: set[str] = set()
    for raw in body.items:
        text = (raw or "").strip()
        if not text:
            continue
        # 分享口令常被 App 换行截断（典型：小红书复制出来是两行、只有一行带链接）。
        # 按链接边界切段：纯文本行只作为上下文跟随，不单独成条 —— 否则用户会把
        # 「一条口令」看到「两条结果」（第二条还必然解析失败）。
        for segment in split_link_segments(text):
            match = URL_RE.search(segment)
            # 用「这段口令实际要解析的链接」去重，而不是整段文本
            key = match.group(0) if match else segment
            if key in seen:
                continue
            seen.add(key)
            items.append(segment)
    items = items[: batch.MAX_ITEMS]
    if not items:
        raise EMPTY_INPUT

    job = batch.STORE.create(items, source=source, client_ip=client_ip)
    asyncio.create_task(_run_batch_job(job))
    return {"ok": True, "jobId": job.id, "total": job.total}


@app.get("/api/batch/{job_id}", tags=["解析"], summary="查询批量解析进度与结果")
async def batch_get(job_id: str):
    """查询批量 job：逐条状态与结果，前端据此渲染进度条与结果卡片。"""
    snapshot = batch.STORE.get(job_id)
    if snapshot is None:
        raise NOT_FOUND
    return {"ok": True, "job": snapshot}


@app.get("/api/media/{token}", tags=["媒体"], summary="媒体代理（在线预览）")
async def media(token: str, request: Request):
    return await _proxy(token, as_attachment=False, range_header=request.headers.get("range"))


@app.get("/api/download/{token}", tags=["媒体"], summary="下载单个媒体")
async def download(token: str):
    return await _proxy(token, as_attachment=True)


def _pack_err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


@app.post(
    "/api/batch/{job_id}/zip",
    tags=["媒体"],
    summary="发起打包 ZIP",
    dependencies=[Depends(guard.batch_guard)],
)
async def batch_zip(job_id: str):
    """触发批量打包：把批量成功结果里的媒体抓取后打包成一键下载的 zip。

    异步任务，立即返回打包状态；拉取走 `_open_media`（候选重试 + 签名刷新），
    逐条失败只跳过该文件，不中断整体；有单条/整体体积上限与保留时限。
    """
    job_snapshot = batch.STORE.get(job_id)
    if job_snapshot is None:
        return _pack_err(404, "JOB_NOT_FOUND", "批量任务不存在或已过期")
    if job_snapshot.get("status") != "done":
        return _pack_err(409, "JOB_NOT_READY", "批量解析尚未完成，请稍后")

    existing = pack.get_pack(job_id)
    if existing is not None:
        if existing.status == "running":
            return _pack_err(409, "PACK_RUNNING", "打包进行中，请稍候")
        if existing.status == "done":
            return {"ok": True, "pack": existing.as_dict()}

    media = pack.collect_media(job_snapshot)
    if not media:
        return _pack_err(409, "NO_MEDIA", "没有可打包的媒体")

    pack_state = pack.start_pack(job_id, total=len(media))
    asyncio.create_task(pack.run_pack(pack_state, job_id))
    return {"ok": True, "pack": pack_state.as_dict()}


@app.get("/api/batch/{job_id}/zip", tags=["媒体"], summary="查询打包进度")
async def batch_zip_status(job_id: str):
    """查询打包进度：pulled/total、bytes、是否可下载。"""
    pack_state = pack.get_pack(job_id)
    if pack_state is None:
        return _pack_err(404, "PACK_NOT_FOUND", "尚未打包")
    return {"ok": True, "pack": pack_state.as_dict()}


@app.get("/api/batch/{job_id}/zip/download", tags=["媒体"], summary="下载打包 ZIP")
async def batch_zip_download(job_id: str):
    """下载打包好的 zip。无法下载时返回结构化错误，便于前端给用户提示。"""
    pack_state = pack.get_pack(job_id)
    if pack_state is None or pack_state.status != "done" or not pack_state.zip_path:
        return _pack_err(404, "PACK_NOT_READY", "打包产物不存在或已过期")
    path = Path(pack_state.zip_path)
    if not path.is_file():
        return _pack_err(404, "PACK_NOT_READY", "打包产物不存在或已过期")
    return FileResponse(path, media_type="application/zip", filename=f"batch_download_{job_id}.zip")
