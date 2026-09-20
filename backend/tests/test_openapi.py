"""OpenAPI 文档完整性测试。

FastAPI 会自动生成 OpenAPI，但「每个端点都有分组与摘要」「分组都有描述」需要人工维护——
这里固化成测试，避免后续新增接口时漏写文档。
"""
from __future__ import annotations

from app.main import app

_METHODS = {"get", "post", "put", "delete", "patch"}


def _spec() -> dict:
    return app.openapi()


def test_openapi_info_metadata():
    info = _spec()["info"]
    assert info["title"]
    assert info["description"]
    assert info["version"]


def test_declared_tags_have_description():
    tags = _spec().get("tags") or []
    assert tags
    for tag in tags:
        assert tag.get("description"), f"tag 缺描述: {tag.get('name')}"


def test_every_operation_has_tag_and_summary():
    spec = _spec()
    declared = {t["name"] for t in spec.get("tags") or []}
    problems: list[str] = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in _METHODS:
                continue
            if not op.get("summary"):
                problems.append(f"{method.upper()} {path} 缺 summary")
            op_tags = op.get("tags") or []
            if not op_tags:
                problems.append(f"{method.upper()} {path} 缺 tags")
            for tag in op_tags:
                if tag not in declared:
                    problems.append(f"{method.upper()} {path} 用了未声明的 tag: {tag}")
    assert not problems, problems
