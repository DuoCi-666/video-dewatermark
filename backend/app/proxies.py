"""按平台解析「出口代理」配置。

背景：部分平台按出口 IP 地理位置做限制，海外机器上会解析失败（B 站的
HTTP 412、米游社的 TCP 连不上），同代码在国内机房正常。给这些平台单独
指定一个国内出口的代理即可绕开。

代理 URL 支持 ``http://`` / ``https://`` 与 ``socks5://`` / ``socks5h://``
（SOCKS5 由依赖 ``socksio`` 提供，见 requirements.txt）。``socks5h://`` 的
``h`` 表示域名交给代理远端解析，适合出口侧能直连目标域名的场景。

约定（环境变量，按优先级）：

1. ``<平台大写>_PROXY``：只对该平台生效，如 ``MIYOUSHE_PROXY``；
   ``bilibili`` 还兼容历史名 ``BILI_PROXY``（已对外文档化，勿改）
2. ``PARSE_PROXY``：兜底，对所有平台生效（一般只在「整机在海外、所有
   平台都要走代理」时用）

都不配置 = 直连，行为与旧版完全一致。
"""

from __future__ import annotations

import os

# 已对外文档化的历史变量名（保持兼容，不要移除）
_ENV_OVERRIDES: dict[str, str] = {
    "bilibili": "BILI_PROXY",
}


def _candidate_names(platform: str) -> list[str]:
    names: list[str] = []
    override = _ENV_OVERRIDES.get(platform)
    if override:
        names.append(override)
    names.append(f"{platform.upper()}_PROXY")
    return names


def proxy_for(platform: str) -> str:
    """返回该平台应使用的代理 URL；未配置时返回空串（表示直连）。"""
    for name in _candidate_names(platform):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return (os.environ.get("PARSE_PROXY") or "").strip()


def proxied_client_kwargs(platform: str) -> dict:
    """给 httpx.AsyncClient 用的代理参数（未配置时为空 dict）。"""
    proxy = proxy_for(platform)
    return {"proxy": proxy} if proxy else {}
