"""限流与请求防护的测试。

限流默认由 conftest 关闭，用例内部按需开启并设定阈值；

    UNSUPPORTED 是一个「不支持的平台」输入：防护依赖在解析之前执行，
    所以它能消耗配额、触发 429，又不会真的发起任何网络请求。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import guard as guard_module
from app import main as main_module
from app.main import app

client = TestClient(app)
# 未捕获异常会被 Starlette 重新抛出，测试里用这个 client 读取 500 响应体
quiet_client = TestClient(app, raise_server_exceptions=False)

UNSUPPORTED = {"input": "https://www.youtube.com/watch?v=1"}


@pytest.fixture
def g():
    return guard_module.GUARD


# ---------------------------------------------------------------- 单元行为
def test_token_bucket_refills_over_time():
    bucket = guard_module.TokenBucket(rate_per_sec=1000.0, capacity=1)
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is False
    time.sleep(0.01)  # 1000/s * 0.01s = 10 个令牌
    assert bucket.allow("ip") is True


def test_concurrency_acquire_and_release():
    guard = guard_module.Guard(enabled=True, concurrency=2)
    assert guard.acquire() is True
    assert guard.acquire() is True
    assert guard.acquire() is False
    assert guard.active == 2
    guard.release()
    assert guard.active == 1
    assert guard.acquire() is True
    guard.release()
    guard.release()
    assert guard.active == 0


def test_guard_reads_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("PARSE_RATE_PER_MIN", "7")
    monkeypatch.setenv("PARSE_BURST", "3")
    monkeypatch.setenv("PARSE_CONCURRENCY", "2")
    guard = guard_module.Guard.from_env()
    assert (guard.rate_per_min, guard.burst, guard.concurrency) == (7, 3, 2)
    monkeypatch.setenv("GUARD_ENABLED", "0")
    assert guard_module.Guard.from_env().enabled is False


# ---------------------------------------------------------------- 限流
def test_rate_limit_returns_429(g):
    g.configure(enabled=True, rate_per_min=60, burst=1)

    first = client.post("/api/parse", json=UNSUPPORTED)
    assert first.status_code == 400  # 正常走到解析前的校验，用掉 1 个令牌

    second = client.post("/api/parse", json=UNSUPPORTED)
    assert second.status_code == 429
    body = second.json()
    assert body["ok"] is False
    assert body["code"] == "RATE_LIMITED"
    assert second.headers.get("retry-after")
    assert g.rejected_rate == 1


def test_rate_limit_is_per_client_ip(g):
    g.configure(enabled=True, rate_per_min=60, burst=1)
    a = {"x-real-ip": "1.1.1.1"}
    b = {"x-real-ip": "2.2.2.2"}

    assert client.post("/api/parse", json=UNSUPPORTED, headers=a).status_code == 400
    assert client.post("/api/parse", json=UNSUPPORTED, headers=b).status_code == 400
    # A 的配额已耗尽，B 不受影响
    assert client.post("/api/parse", json=UNSUPPORTED, headers=a).status_code == 429
    assert client.post("/api/parse", json=UNSUPPORTED, headers=b).status_code == 429


def test_forwarded_for_uses_first_hop(g):
    g.configure(enabled=True, rate_per_min=60, burst=1)
    chain = {"x-forwarded-for": "5.5.5.5, 6.6.6.6"}

    assert client.post("/api/parse", json=UNSUPPORTED, headers=chain).status_code == 400
    assert client.post("/api/parse", json=UNSUPPORTED, headers=chain).status_code == 429
    # 链条里的另一跳是独立的桶
    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-forwarded-for": "6.6.6.6"}).status_code == 400


def test_real_ip_wins_over_forwarded_for(g):
    g.configure(enabled=True, rate_per_min=60, burst=1)
    headers = {"x-real-ip": "7.7.7.7", "x-forwarded-for": "8.8.8.8"}

    assert client.post("/api/parse", json=UNSUPPORTED, headers=headers).status_code == 400
    assert client.post("/api/parse", json=UNSUPPORTED, headers=headers).status_code == 429
    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-real-ip": "8.8.8.8"}).status_code == 400


def test_proxy_header_trust_enabled_by_default(g, monkeypatch):
    """默认信任反代写的 IP 头（nginx 部署形态）：不同 IP 各自独立配额。"""
    monkeypatch.delenv("GUARD_TRUST_PROXY_HEADERS", raising=False)
    g.configure(enabled=True, rate_per_min=60, burst=1)

    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-real-ip": "1.1.1.1"}).status_code == 400
    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-real-ip": "2.2.2.2"}).status_code == 400


def test_proxy_header_trust_can_be_disabled(g, monkeypatch):
    """裸部署（前面没有反向代理）时应关掉头信任：伪造 X-Real-IP 不能再换桶绕限流。"""
    monkeypatch.setenv("GUARD_TRUST_PROXY_HEADERS", "0")
    g.configure(enabled=True, rate_per_min=60, burst=1)

    # 两个不同的伪造 IP 都落到同一个桶（回退到真实 client.host），第二次即被限流
    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-real-ip": "1.1.1.1"}).status_code == 400
    assert client.post("/api/parse", json=UNSUPPORTED, headers={"x-real-ip": "2.2.2.2"}).status_code == 429


# ---------------------------------------------------------------- 并发
def test_concurrency_limit_returns_503(g):
    g.configure(enabled=True, concurrency=1, rate_per_min=10000, burst=100)
    assert g.acquire() is True  # 模拟已有一个解析请求在跑
    try:
        response = client.post("/api/parse", json=UNSUPPORTED)
        assert response.status_code == 503
        assert response.json()["code"] == "SERVER_BUSY"
        assert g.rejected_busy == 1
    finally:
        g.release()


def test_concurrency_slot_is_released_after_request(g):
    g.configure(enabled=True, concurrency=1, rate_per_min=10000, burst=100)
    for _ in range(3):
        assert client.post("/api/parse", json=UNSUPPORTED).status_code == 400
    assert g.active == 0


def test_guard_disabled_lets_traffic_through(g):
    g.configure(enabled=False, rate_per_min=1, burst=1)
    for _ in range(5):
        assert client.post("/api/parse", json=UNSUPPORTED).status_code == 400


# ---------------------------------------------------------------- 错误兜底
def test_unexpected_error_returns_structured_500(g, monkeypatch):
    monkeypatch.setattr(main_module, "extract_share_url", lambda text: ("xiaohongshu", "https://example.com/x"))

    async def boom(*args, **kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(main_module, "parse_xiaohongshu", boom)

    response = quiet_client.post("/api/parse", json={"input": "whatever"})
    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "INTERNAL_ERROR"
    assert "sensitive internal detail" not in response.text  # 不泄漏内部信息
    assert g.active == 0  # 异常路径也必须归还并发名额


def test_invalid_payload_returns_structured_400():
    response = client.post("/api/parse", json={"input": "x" * 2001})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_INPUT"


def test_parse_error_keeps_its_own_code():
    response = client.post("/api/parse", json=UNSUPPORTED)
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_PLATFORM"


def test_health_exposes_guard_stats():
    body = client.get("/api/health").json()
    assert body["ok"] is True
    guard_stats = body["guard"]
    assert guard_stats["enabled"] in (True, False)
    assert guard_stats["concurrency"] >= 1
    assert "rejected_rate" in guard_stats
