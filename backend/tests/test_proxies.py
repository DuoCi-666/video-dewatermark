from __future__ import annotations

import pytest

from app import proxies


def test_platform_specific_env_wins(monkeypatch):
    monkeypatch.setenv("BILI_PROXY", "http://bili:1")
    monkeypatch.setenv("MIYOUSHE_PROXY", "http://miyoushe:2")
    monkeypatch.setenv("PARSE_PROXY", "http://fallback:9")

    assert proxies.proxy_for("bilibili") == "http://bili:1"
    assert proxies.proxy_for("miyoushe") == "http://miyoushe:2"


def test_bilibili_legacy_env_name_still_works(monkeypatch):
    """已对外文档化的 BILI_PROXY 必须保持兼容（香港生产环境在用）。"""
    monkeypatch.setenv("BILI_PROXY", "http://legacy:7")
    monkeypatch.delenv("BILIBILI_PROXY", raising=False)
    monkeypatch.delenv("PARSE_PROXY", raising=False)

    assert proxies.proxy_for("bilibili") == "http://legacy:7"


def test_bilibili_new_style_env_name_also_works(monkeypatch):
    monkeypatch.delenv("BILI_PROXY", raising=False)
    monkeypatch.setenv("BILIBILI_PROXY", "http://newstyle:8")
    monkeypatch.delenv("PARSE_PROXY", raising=False)

    assert proxies.proxy_for("bilibili") == "http://newstyle:8"


def test_fallback_to_parse_proxy(monkeypatch):
    monkeypatch.delenv("BILI_PROXY", raising=False)
    monkeypatch.delenv("BILIBILI_PROXY", raising=False)
    monkeypatch.setenv("PARSE_PROXY", "http://fallback:9")

    assert proxies.proxy_for("bilibili") == "http://fallback:9"


def test_no_proxy_configured_returns_empty(monkeypatch):
    for name in ("BILI_PROXY", "BILIBILI_PROXY", "MIYOUSHE_PROXY", "PARSE_PROXY"):
        monkeypatch.delenv(name, raising=False)

    assert proxies.proxy_for("bilibili") == ""
    assert proxies.proxied_client_kwargs("miyoushe") == {}


def test_blank_value_treated_as_unset(monkeypatch):
    monkeypatch.setenv("BILI_PROXY", "   ")
    monkeypatch.delenv("PARSE_PROXY", raising=False)

    assert proxies.proxy_for("bilibili") == ""


def test_proxied_client_kwargs(monkeypatch):
    monkeypatch.setenv("MIYOUSHE_PROXY", "http://x:5")

    assert proxies.proxied_client_kwargs("miyoushe") == {"proxy": "http://x:5"}


def test_socks5h_proxy_passes_through(monkeypatch):
    """国内出口换成 SOCKS5（socks5h://，域名交代理远端解析）时原样透传。"""
    monkeypatch.setenv("BILI_PROXY", "socks5h://210.16.182.141:1080")
    monkeypatch.setenv("MIYOUSHE_PROXY", "socks5h://210.16.182.141:1080")

    assert proxies.proxied_client_kwargs("bilibili") == {
        "proxy": "socks5h://210.16.182.141:1080"
    }
    assert proxies.proxied_client_kwargs("miyoushe") == {
        "proxy": "socks5h://210.16.182.141:1080"
    }


def test_socks5h_client_construction_supported():
    """依赖装齐（socksio）：httpx 能用 socks5h:// 建客户端，不再 ImportError。

    这是对 requirements 里 socksio 的回归护栏——缺它时海外 SOCKS5 出口直接不可用。
    """
    import httpx

    client = httpx.Client(proxy="socks5h://127.0.0.1:1080")
    try:
        assert client is not None
    finally:
        client.close()
