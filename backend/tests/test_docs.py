"""文档一致性测试：README 的平台清单必须与代码注册表（PLATFORMS）保持同步。

新增/删除平台时若忘了同步 README，这里会直接失败——把"人工核对"变成"自动校验"，
避免文档漂移（顺带保证 README 表格顺序与前端展示顺序一致）。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.parsers.extract import PLATFORMS

README_PATH = Path(__file__).resolve().parents[2] / "README.md"

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _code_names() -> list[str]:
    return [p.name for p in PLATFORMS]


def _cn_to_int(text: str) -> int | None:
    if not text:
        return None
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(text)


def _feature_line() -> str:
    return next(
        line for line in _readme().splitlines() if "平台支持" in line and line.startswith("- ")
    )


def test_readme_platform_table_matches_code_order():
    block = _readme().split("## 🧭 支持平台", 1)[1].split("\n>", 1)[0]
    rows: list[str] = []
    for line in block.splitlines():
        if line.startswith("|") and line.count("|") >= 4:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells[0] != "平台" and not set(cells[0]) <= set("-: "):
                rows.append(cells[0])
    assert rows == _code_names()


def test_readme_title_lists_all_platforms():
    title = _readme().splitlines()[4]
    names = [n.strip() for n in title.replace("**", "").split("—")[-1].split("·")]
    assert set(names) == set(_code_names())


def test_readme_feature_line_lists_all_platforms():
    inner = re.search(r"\*\*[^*]*\*\* —— (.*?)，整段分享口令", _feature_line())
    assert inner is not None
    names = [re.match(r"([^（(]+)", x.strip()).group(1) for x in inner.group(1).split("、")]
    assert set(names) == set(_code_names())
    assert len(names) == len(_code_names())


def test_readme_platform_count_wording_matches():
    match = re.search(r"\*\*([一二三四五六七八九十]+)平台支持\*\*", _feature_line())
    assert match is not None
    assert _cn_to_int(match.group(1)) == len(PLATFORMS)
