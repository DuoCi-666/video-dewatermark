"""请求防护：按 IP 限流 + 解析并发上限 + 统一错误兜底。

设计取舍
--------
限流只挂在 ``/api/parse``（路由级依赖），不使用全局 HTTP 中间件：
``/api/media`` 是耗时上百秒的视频流，全局中间件（Starlette BaseHTTPMiddleware）
会包一层流式转发，既增加开销也可能干扰 Range 流式响应。媒体侧的带宽/连接
保护交给 nginx 的 ``limit_conn``。

单进程 uvicorn 部署，状态全部存内存；阈值可用环境变量覆盖：

    GUARD_ENABLED=0       关闭限流（默认开启）
    PARSE_RATE_PER_MIN    解析接口每 IP 每分钟配额（默认 120）
    PARSE_BURST           突发额度，即令牌桶容量（默认 30）
    PARSE_CONCURRENCY     解析接口全局并发上限（默认 8）
"""
from __future__ import annotations

import logging
import os
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import INTERNAL_ERROR, INVALID_INPUT, RATE_LIMITED, SERVER_BUSY

logger = logging.getLogger("app.guard")

# 空闲多久后回收某个 IP 的令牌桶
IDLE_TTL_SEC = 300.0
# 惰性清理的最小间隔
SWEEP_INTERVAL_SEC = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是整数，回退到默认值 %s", name, raw, default)
        return default


def _truthy(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _trust_proxy_headers() -> bool:
    """是否信任反向代理写入的客户端 IP 头（``X-Real-IP`` / ``X-Forwarded-For``）。

    默认信任：项目推荐的部署形态是 nginx 反代，nginx 用 ``proxy_set_header``
    覆盖该头，客户端自带的伪造值会被覆盖掉，不影响按 IP 限流。

    **裸部署（前面没有反向代理）时应设 ``GUARD_TRUST_PROXY_HEADERS=0``**：
    否则客户端可以随意伪造 ``X-Real-IP``，把限流的 IP 桶键变成自己可控的值，
    从而绕过按 IP 限流。
    """
    return _truthy(os.environ.get("GUARD_TRUST_PROXY_HEADERS", "1"))


def client_ip(request: Request) -> str:
    """取真实客户端 IP。反代会写入 X-Real-IP / X-Forwarded-For（见上方开关）。"""
    if _trust_proxy_headers():
        real = (request.headers.get("x-real-ip") or "").strip()
        if real:
            return real
        forwarded = (request.headers.get("x-forwarded-for") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class TokenBucket:
    """按 key 计数的令牌桶。rate 为每秒补充量，capacity 为突发上限。"""

    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = max(rate_per_sec, 0.0)
        self.capacity = max(capacity, 1)
        self._state: dict[str, tuple[float, float]] = {}
        self._swept_at = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, stamp = self._state.get(key, (float(self.capacity), now))
        tokens = min(float(self.capacity), tokens + (now - stamp) * self.rate)
        if tokens < 1.0:
            self._state[key] = (tokens, now)
            self._sweep(now)
            return False
        self._state[key] = (tokens - 1.0, now)
        self._sweep(now)
        return True

    def _sweep(self, now: float) -> None:
        if now - self._swept_at < SWEEP_INTERVAL_SEC:
            return
        self._swept_at = now
        deadline = now - IDLE_TTL_SEC
        for key in [k for k, (_, stamp) in self._state.items() if stamp < deadline]:
            self._state.pop(key, None)

    def size(self) -> int:
        return len(self._state)

    def clear(self) -> None:
        self._state.clear()
        self._swept_at = time.monotonic()


class Guard:
    """按 IP 限流 + 全局并发上限。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        rate_per_min: int = 120,
        burst: int = 30,
        concurrency: int = 8,
        batch_rate_per_min: int = 20,
        batch_burst: int = 5,
    ) -> None:
        self.enabled = enabled
        self.rate_per_min = max(int(rate_per_min), 0)
        self.burst = max(int(burst), 1)
        self.concurrency = max(int(concurrency), 1)
        self.bucket = TokenBucket(self.rate_per_min / 60.0, self.burst)
        # 批量接口独立配额：一次批量会放大 N 倍上游请求，不能挤占单条用户
        self.batch_rate_per_min = max(int(batch_rate_per_min), 0)
        self.batch_burst = max(int(batch_burst), 1)
        self.batch_bucket = TokenBucket(self.batch_rate_per_min / 60.0, self.batch_burst)
        self._active = 0
        self.rejected_rate = 0
        self.rejected_busy = 0
        self.rejected_rate_batch = 0

    @classmethod
    def from_env(cls) -> "Guard":
        return cls(
            enabled=_truthy(os.environ.get("GUARD_ENABLED", "1")),
            rate_per_min=_env_int("PARSE_RATE_PER_MIN", 120),
            burst=_env_int("PARSE_BURST", 30),
            concurrency=_env_int("PARSE_CONCURRENCY", 8),
            batch_rate_per_min=_env_int("BATCH_RATE_PER_MIN", 20),
            batch_burst=_env_int("BATCH_BURST", 5),
        )

    def configure(
        self,
        *,
        enabled: bool | None = None,
        rate_per_min: int | None = None,
        burst: int | None = None,
        concurrency: int | None = None,
        batch_rate_per_min: int | None = None,
        batch_burst: int | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if rate_per_min is not None:
            self.rate_per_min = max(int(rate_per_min), 0)
        if burst is not None:
            self.burst = max(int(burst), 1)
        if concurrency is not None:
            self.concurrency = max(int(concurrency), 1)
        if batch_rate_per_min is not None:
            self.batch_rate_per_min = max(int(batch_rate_per_min), 0)
        if batch_burst is not None:
            self.batch_burst = max(int(batch_burst), 1)
        self.bucket = TokenBucket(self.rate_per_min / 60.0, self.burst)
        self.batch_bucket = TokenBucket(self.batch_rate_per_min / 60.0, self.batch_burst)

    @property
    def active(self) -> int:
        return self._active

    def acquire(self) -> bool:
        if self._active >= self.concurrency:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)

    def reset(self) -> None:
        self._active = 0
        self.rejected_rate = 0
        self.rejected_busy = 0
        self.rejected_rate_batch = 0
        self.bucket.clear()
        self.batch_bucket.clear()

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "active": self._active,
            "rate_per_min": self.rate_per_min,
            "burst": self.burst,
            "concurrency": self.concurrency,
            "batch_rate_per_min": self.batch_rate_per_min,
            "batch_burst": self.batch_burst,
            "rejected_rate": self.rejected_rate,
            "rejected_busy": self.rejected_busy,
            "rejected_rate_batch": self.rejected_rate_batch,
            "tracked_ips": self.bucket.size(),
        }


GUARD = Guard.from_env()


async def parse_guard(request: Request) -> AsyncIterator[None]:
    """挂在 /api/parse 上的防护依赖：先限流，再占并发名额。"""
    if not GUARD.enabled:
        yield
        return

    ip = client_ip(request)
    if not GUARD.bucket.allow(ip):
        GUARD.rejected_rate += 1
        logger.warning("限流拦截 ip=%s path=%s", ip, request.url.path)
        raise RATE_LIMITED

    if not GUARD.acquire():
        GUARD.rejected_busy += 1
        logger.warning("并发上限拦截 ip=%s active=%s", ip, GUARD.active)
        raise SERVER_BUSY

    try:
        yield
    finally:
        GUARD.release()


async def batch_guard(request: Request) -> AsyncIterator[None]:
    """挂在 POST /api/batch 上的防护依赖：仅独立的批量限流，不占全局并发名额。

    /api/parse 的全局并发(8)是给「一次一个」的单条解析预留的；批量一次会放大
    N 倍上游请求，所以走这里独立的批量令牌桶，让批量不要挤占单条用户。
    批量内部的解析并发由 app.batch 的全局信号量（BATCH_CONCURRENCY）单独控制。
    """
    if not GUARD.enabled:
        yield
        return

    ip = client_ip(request)
    if not GUARD.batch_bucket.allow(ip):
        GUARD.rejected_rate_batch += 1
        logger.warning("批量限流拦截 ip=%s path=%s", ip, request.url.path)
        raise RATE_LIMITED

    yield


def register_error_handlers(app: FastAPI) -> None:
    """统一错误兜底：参数校验失败返回结构化 400，未预期异常返回结构化 500。"""

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        logger.info("参数校验失败 path=%s detail=%s", request.url.path, exc.errors()[:2])
        return JSONResponse(
            status_code=INVALID_INPUT.status,
            content={"ok": False, "code": INVALID_INPUT.code, "message": INVALID_INPUT.message},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        # 只回结构化错误，堆栈留在服务端日志
        logger.exception("未捕获异常 path=%s", request.url.path)
        return JSONResponse(
            status_code=INTERNAL_ERROR.status,
            content={"ok": False, "code": INTERNAL_ERROR.code, "message": INTERNAL_ERROR.message},
        )
