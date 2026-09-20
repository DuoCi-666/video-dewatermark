"""共享 HTTP 客户端容器。

原先把 PARSE_CLIENT / PROXY_CLIENT 直接放在 main.py 里，拆分模块后解析编排
（parsing）与媒体代理（media）都要用它们——若各自从 main 导入就会形成循环依赖。

这里用一个独立的可变容器持有引用：由 lifespan 在启动时注入、关闭时置空，
各业务模块只依赖本模块，不依赖 main。
"""
from __future__ import annotations

import httpx

# 共享客户端：None 表示尚未初始化（或已关闭）。
# 业务代码拿到 None 时应自行新建临时客户端，保证测试与单次调用也能工作。
_parse_client: httpx.AsyncClient | None = None
_proxy_client: httpx.AsyncClient | None = None


def get_parse_client() -> httpx.AsyncClient | None:
    """解析用客户端（短超时）。未初始化时返回 None。"""
    return _parse_client


def get_proxy_client() -> httpx.AsyncClient | None:
    """媒体回源用客户端（长超时、流式）。未初始化时返回 None。"""
    return _proxy_client


def set_parse_client(client: httpx.AsyncClient | None) -> None:
    global _parse_client
    _parse_client = client


def set_proxy_client(client: httpx.AsyncClient | None) -> None:
    global _proxy_client
    _proxy_client = client
