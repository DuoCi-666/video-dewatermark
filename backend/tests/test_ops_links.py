"""巡检样本（ops/links.json）合法性测试（离线，不联网）。

样本链接本身会随作品删除 / 短链过期而失效，所以这里**只校验结构**：
- platform 必须是已注册的平台 key（防止拼错或平台下线后残留）；
- url 必须是 http(s)；
- expect 只能是 None / video / images。

另外把"是否每个平台都有巡检样本"也纳入守卫：已知 3 个缺口先豁免，
**以后新增平台若忘了补巡检样本，这里会直接失败**。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.parsers.extract import PLATFORMS

LINKS_PATH = Path(__file__).resolve().parents[2] / "ops" / "links.json"

# 已知缺口：需要从对应 App 分享获取真实链接后补齐（当前已全部补齐）
KNOWN_GAPS: set[str] = set()


def _items() -> dict:
    data = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def test_links_json_is_valid_and_nonempty():
    assert _items(), "巡检样本不能为空"


def test_every_sample_platform_is_registered():
    keys = {p.key for p in PLATFORMS}
    unknown = sorted({v.get("platform") for v in _items().values()} - keys)
    assert not unknown, f"巡检样本引用了未注册平台: {unknown}"


def test_every_sample_has_http_url():
    bad = [k for k, v in _items().items() if not str(v.get("url", "")).startswith("http")]
    assert not bad, f"样本缺少合法 url: {bad}"


def test_expect_values_are_sane():
    allowed = {None, "video", "images"}
    bad = [k for k, v in _items().items() if v.get("expect") not in allowed]
    assert not bad, f"expect 取值非法: {bad}"


def test_all_platforms_covered_except_known_gaps():
    keys = {p.key for p in PLATFORMS}
    covered = {v.get("platform") for v in _items().values()}
    missing = set(keys) - covered
    assert missing <= KNOWN_GAPS, (
        f"以下平台还没有巡检样本，且不在已知缺口里: {sorted(missing - KNOWN_GAPS)}"
    )
