"""平台注册一致性测试。

新增一个平台要动多处置（hosts 常量、PLATFORMS 元组、parsers/xxx.py、
main 的 import、parsing._parsers() 映射、前端展示），漏一处就是线上 bug。
历史上真出过：`_parsers()` 漏了 weibo / jimeng / pipix 三个平台的映射，
平台能识别、能显示在清单里，但一解析就 KeyError。

这里把「所有同步点必须指向同一份平台清单」变成机器校验的断言，让这类
遗漏在 CI 阶段就报错，而不是等用户来点。

约定：`app.parsers.extract.PLATFORMS` 是平台清单的**唯一事实来源**，
其余位置都必须是它的派生。
"""
from __future__ import annotations

import importlib

import pytest

from app import main, parsing
from app.parsers.extract import (
    PLATFORMS,
    SIGNED_MEDIA_PLATFORMS,
    platform_list,
    signed_media_platforms,
)

DECLARED_KEYS = [item.key for item in PLATFORMS]
DECLARED_KEY_SET = set(DECLARED_KEYS)


def _parser_module_for(key: str):
    """按约定：平台 key -> app.parsers.<key> 模块（xiaohongshu 除外见下）。"""
    module_name = {"xiaohongshu": "xhs"}.get(key, key)
    return importlib.import_module(f"app.parsers.{module_name}")


def test_declared_keys_are_unique():
    """平台 key 不可重复 —— 重复会让识别路由到不确定的解析器。"""
    assert len(DECLARED_KEYS) == len(DECLARED_KEY_SET)


def test_declared_keys_are_ascii_identifiers():
    """平台 key 必须能安全用于模块名 / URL 参数 / 字典键。"""
    for key in DECLARED_KEYS:
        assert key.isascii(), f"{key!r} 含非 ASCII 字符"
        assert key.replace("_", "").isalnum(), f"{key!r} 不是合法标识符"
        assert key.islower(), f"{key!r} 应全小写"


def test_declared_names_and_hosts_not_empty():
    """每个平台都要有展示名与至少一个识别域名，否则前端徽标 / 识别都会失真。"""
    for item in PLATFORMS:
        assert item.name and item.name.strip(), f"{item.key} 缺少展示名"
        assert item.hosts, f"{item.key} 未声明任何识别域名"
        for host in item.hosts:
            assert host and host.strip() and "." in host, f"{item.key} 域名异常: {host!r}"


def test_declared_kinds_are_known():
    """内容类型只允许 video / images —— 解析器与前端按此枚举分支。"""
    for item in PLATFORMS:
        assert item.kinds, f"{item.key} 未声明内容类型"
        for kind in item.kinds:
            assert kind in ("video", "images"), f"{item.key} 出现未知类型 {kind!r}"


def test_every_declared_platform_has_a_parser_module():
    """每个声明的平台都要有可导入的 parsers/<key>.py 且导出 parse 可调用。

    这是「声明了但没实现」的第一道闸门。
    """
    for key in DECLARED_KEYS:
        module = _parser_module_for(key)
        assert hasattr(module, "parse"), f"app.parsers.{key} 未导出 parse"
        assert callable(module.parse), f"app.parsers.{key}.parse 不可调用"


def test_parsers_mapping_covers_every_declared_platform():
    """_parsers() 必须与 PLATFORMS 一一对应（双向，不允许多也不允许漏）。

    这正是历史上 weibo / jimeng / pipix 被漏掉的那处 bug 的守卫。
    """
    mapped = set(parsing._parsers().keys())
    missing = DECLARED_KEY_SET - mapped
    extra = mapped - DECLARED_KEY_SET
    assert not missing, f"_parsers() 漏了这些平台，解析会 KeyError: {sorted(missing)}"
    assert not extra, f"_parsers() 多出未声明的平台，平台清单一真源被绕过: {sorted(extra)}"


def test_parsers_mapping_values_are_main_attributes():
    """_parsers() 每个值都必须等于 app.main.parse_xxx 当前属性。

    解析器选择刻意做成「每次调用时动态读取 main 上的属性」，测试与部署者会
    monkeypatch app.main.parse_xxx。若这里绑定成了别处的函数对象，
    补丁就会失效 —— 保存这个不变量。
    """
    for key, fn in parsing._parsers().items():
        expected = getattr(main, f"parse_{key}", None)
        assert expected is not None, f"app.main 未导出 parse_{key}（_parsers 会取不到）"
        assert fn is expected, (
            f"{key}: _parsers() 返回的解析器不是 app.main.parse_{key}，"
            "monkeypatch 将无法生效"
        )


def test_main_exposes_parse_fn_for_every_declared_platform():
    """app.main.parse_<key> 必须存在（_parsers 动态读取依赖它）。"""
    for key in DECLARED_KEYS:
        attr = f"parse_{key}"
        assert hasattr(main, attr), f"app.main 缺少 {attr}"
        assert callable(getattr(main, attr)), f"app.main.{attr} 不可调用"


def test_signed_media_set_is_derived_from_platforms():
    """签名平台集合必须由 PLATFORMS 派生，不能各写一份。"""
    assert SIGNED_MEDIA_PLATFORMS == signed_media_platforms()
    expected = {item.key for item in PLATFORMS if item.signed_media}
    assert SIGNED_MEDIA_PLATFORMS == expected
    # 只含已声明平台
    assert SIGNED_MEDIA_PLATFORMS <= DECLARED_KEY_SET


def test_signed_media_requires_video_or_images():
    """签名标记只对会签发媒体 token 的内容类型有意义（当前两者都签发）。"""
    for item in PLATFORMS:
        if item.signed_media:
            assert item.kinds, f"{item.key} 标记了 signed_media 却没有内容类型"


def test_platform_list_matches_declarations():
    """给前端的清单必须与声明逐项一致（key/name/hosts/kinds 全等，顺序一致）。"""
    api_list = platform_list()
    assert len(api_list) == len(PLATFORMS)
    for api_item, declared in zip(api_list, PLATFORMS):
        assert api_item["key"] == declared.key
        assert api_item["name"] == declared.name
        assert api_item["hosts"] == list(declared.hosts)
        assert api_item["kinds"] == list(declared.kinds)


def test_api_platforms_endpoint_matches_declarations():
    """/api/platforms 下发的内容也要与前端的展示口径一致。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/platforms")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    keys = [item["key"] for item in payload["platforms"]]
    assert keys == DECLARED_KEYS


@pytest.mark.parametrize("item", PLATFORMS, ids=[p.key for p in PLATFORMS])
def test_each_platform_recognizes_its_own_host(item):
    """用平台自己的域名造一个链接，识别结果必须是该平台。

    防止「hosts 写了但识别分支没接上」：声明与识别逻辑脱节时这里会红。
    """
    from app.parsers.extract import extract_share_url

    host = item.hosts[0]
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/some/path"
        try:
            key, _ = extract_share_url(url)
        except Exception as exc:  # noqa: BLE001 - 识别失败即断言失败
            pytest.fail(f"{item.key} 无法识别自己的域名 {url}: {exc!r}")
        assert key == item.key, f"{url} 被识别成了 {key}，期望 {item.key}"


def test_new_platform_requires_no_main_or_mapping_edit(monkeypatch):
    """实证「新增平台只需两处改动」这个约定本身。

    模拟流程：造一个 parsers/demo.py（仅内存注册）+ 往 PLATFORMS 追加一行，
    然后断言：
      - _parsers() 自动包含新平台（无需改 parsing.py 的映射）
      - extract_share_url 自动识别新平台域名（无需改识别分支）
      - 不需要在 app.main 里写 parse_demo

    这是对「声明即注册」这一设计承诺的回归保护：将来若有人把 _parsers() 改回
    手写映射，或识别逻辑硬编码平台列表，这条测试会立刻失败。
    """
    import sys
    import types

    from app.parsers import extract

    module_name = "app.parsers._demo_platform"
    fake_module = types.ModuleType(module_name)

    async def parse(url, timeout=15.0, client=None):  # noqa: ARG001 - 签名对齐真实解析器
        return None

    fake_module.parse = parse
    monkeypatch.setitem(sys.modules, module_name, fake_module)

    new_platform = extract.Platform(
        "_demo_platform", "演示平台", ("_demo-platform.example.com",)
    )
    monkeypatch.setattr(
        extract, "PLATFORMS", extract.PLATFORMS + (new_platform,)
    )

    # _parsers() 必须自动跟上（含新平台），且不依赖 app.main.parse_xxx
    parsers = parsing._parsers()
    assert "_demo_platform" in parsers, "_parsers() 没有自动包含新声明平台"
    assert parsers["_demo_platform"] is parse, "新平台解析器未指向 parsers 模块的 parse"

    # 识别也要自动跟上
    key, _ = extract.extract_share_url("https://_demo-platform.example.com/v/1")
    assert key == "_demo_platform"
