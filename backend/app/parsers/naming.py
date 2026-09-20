"""文件名清洗：把作品标题转成可安全落盘的文件名。

原先每个解析器各自带一份 ``filename_from_title``（共 8 份，逻辑完全相同、只有
标题为空时的兜底名不同），这里收敛成唯一实现：调用方通过 ``fallback`` 指定兜底名
（如 ``cover`` / ``video`` / ``image``，或平台名）。
"""
from __future__ import annotations

import re

# 文件系统非法字符 + 空白，统一替换成下划线
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\s]+')
# 文件名主干最大长度（避免超长路径）
MAX_STEM = 40
DEFAULT_FALLBACK = "video"


def filename_from_title(title: str, ext: str, *, fallback: str = DEFAULT_FALLBACK) -> str:
    """清洗标题为文件名：替换非法字符、截断，标题为空时用 ``fallback``。"""
    cleaned = _UNSAFE_CHARS.sub("_", title or "").strip("._")
    stem = cleaned[:MAX_STEM] or fallback
    return f"{stem}.{ext}"
