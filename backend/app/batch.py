"""批量解析 job：内存有界环形存储 + 全局并发信号量。

形态：异步任务接口，而不是同步循环
    单条解析同步返回、有 15s 超时，且`/api/parse` 对每条请求都限流 —— 直接把
    N 条链接同步循环会自我限流，还可能被 nginx 网关超时截断。所以批量做成：
    ``POST /api/batch`` 立即返回 ``job_id``，后台用 asyncio 逐条解析（复用单条
    解析路径，全部平台自动覆盖）；前端轮询 ``GET /api/batch/{id}`` 拿进度与结果。

隔离与配额
    逐条错误隔离：单条失败只标记该项，不影响其他条目。批量不该挤占单条用户的
    配额 —— 创建 job 的限流用 guard 里独立的批量 bucket，解析本身的并发用这里
    的全局信号量（默认并发 3），与 /api/parse 的全局并发(8)分开。

存储
    单实例内存即可：dict + 插入顺序列表，超出 ``BATCH_JOBS_MAX`` 淘汰最老的
    job，超过 ``BATCH_JOB_TTL_SEC`` 自动过期。运行中 job 不会因超上限被淘汰。
    需要持久化时再换 Redis。

环境变量：BATCH_JOBS_MAX / BATCH_JOB_TTL_SEC / BATCH_ITEMS_MAX /
BATCH_CONCURRENCY
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

MAX_JOBS = int(os.environ.get("BATCH_JOBS_MAX", "50"))
JOB_TTL_SEC = int(os.environ.get("BATCH_JOB_TTL_SEC", str(30 * 60)))
MAX_ITEMS = int(os.environ.get("BATCH_ITEMS_MAX", "50"))
# 批量解析的全局并发信号量：独立于 /api/parse 的并发上限，避免互相挤占
CONCURRENCY = max(int(os.environ.get("BATCH_CONCURRENCY", "3")), 1)


def _max_int(value: int, minimum: int) -> int:
    return max(int(value), minimum)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class BatchItem:
    """批量中的一条解析：状态 + 结果（成功含完整 payload，失败含错误码/文案）。"""

    index: int
    text: str
    status: str = "pending"  # pending | running | done
    ok: bool | None = None
    code: str = ""
    message: str = ""
    platform: str = ""
    ms: int = 0
    result: dict | None = None


@dataclass
class Job:
    id: str
    source: str
    client_ip: str
    status: str = "running"  # running | done
    total: int = 0
    done: int = 0
    ok_count: int = 0
    fail_count: int = 0
    created_at: float = 0.0  # monotonic，用于 TTL 淘汰
    created_iso: str = ""
    updated_iso: str = ""
    items: list[BatchItem] = field(default_factory=list)


def _item_to_dict(item: BatchItem) -> dict:
    return {
        "index": item.index,
        "status": item.status,
        "ok": item.ok,
        "code": item.code,
        "message": item.message,
        "platform": item.platform,
        "ms": item.ms,
        "result": item.result,
    }


def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "total": job.total,
        "done": job.done,
        "okCount": job.ok_count,
        "failCount": job.fail_count,
        "createdAt": job.created_iso,
        "updatedAt": job.updated_iso,
        "items": [_item_to_dict(item) for item in job.items],
    }


class JobStore:
    """有界环形 job 存储。单进程 uvicorn 单事件循环内使用，无需加锁。"""

    def __init__(self, *, max_jobs: int = MAX_JOBS, ttl_sec: int = JOB_TTL_SEC) -> None:
        self.max_jobs = _max_int(max_jobs, 1)
        self.ttl_sec = _max_int(ttl_sec, 1)
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []

    def create(self, items: list[str], *, source: str, client_ip: str) -> Job:
        self._sweep()
        job = Job(
            id=secrets.token_hex(12),
            source=source,
            client_ip=client_ip,
            total=len(items),
            created_at=time.monotonic(),
            created_iso=_now_iso(),
            updated_iso=_now_iso(),
            items=[BatchItem(index=i, text=text) for i, text in enumerate(items)],
        )
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._sweep()

        # 有界淘汰：优先淘汰最老的「已完成」job；实在全在跑才按最老淘汰
        while len(self._jobs) - self.max_jobs > 0:
            popped = False
            for oid in list(self._order):
                candidate = self._jobs.get(oid)
                if candidate is not None and candidate.status == "done" and oid != job.id:
                    self._discard(oid)
                    popped = True
                    break
            if not popped:
                self._discard(self._order[0])
        return job

    def get(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        # 访问即续期（对运行中 job 无效；对完成的 job 仅更新展示时间戳）
        if time.monotonic() - job.created_at >= self.ttl_sec and job.status == "done":
            # 已超过 TTL 的已完成 job：直接清除，避免僵尸
            self._discard(job_id)
            return None
        return job_to_dict(job)

    def _discard(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self._order = [oid for oid in self._order if oid in self._jobs]

    def _sweep(self) -> None:
        now = time.monotonic()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.status == "done" and now - job.created_at >= self.ttl_sec
        ]
        for jid in expired:
            self._discard(jid)

    def touch(self, job: Job) -> None:
        job.updated_iso = _now_iso()

    def size(self) -> int:
        return len(self._jobs)

    def reset(self) -> None:
        """仅用于测试：清空所有 job。"""
        self._jobs = {}
        self._order = []


# 批量解析的全局并发信号量（module 级，import 时不绑定 loop，运行时惰性绑定）
SEMAPHORE = asyncio.Semaphore(CONCURRENCY)

STORE = JobStore()