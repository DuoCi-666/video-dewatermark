"""快手单图帖（photoType = SINGLE_PICTURE）的解析测试。

背景：2026-09-12 的失败样本里出现了一条真实用户链接（https://v.kuaishou.com/7GwR5t4V），
页面能正常拿到，但解析报 PARSE_FAILED。原因是单图帖的图片只存在于 coverUrls，
既没有 atlas（图集）也没有 mainMvUrls（视频），而解析器当时只处理了那两条路径。

这里同时守住反向边界：类型未知且没有媒体的页面仍应报 PARSE_FAILED，
不能因为"页面上有张图"就当成图文，否则会把视频解析失败悄悄掩盖掉。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.errors import ParseError
from app.parsers.kuaishou import _is_single_picture, _parse_html

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kuaishou"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_single_picture_post_parses_as_images():
    work = _parse_html(_fixture("single_picture.html"))

    assert work.type == "images"
    assert work.author == "悬铃木"
    assert work.title.startswith("负心者 当诛")
    assert len(work.image_urls) == 1
    assert len(work.image_groups) == 1
    # 单张图也要带上全部 CDN 候选，代理层才能自动 fallback
    assert len(work.image_groups[0]) == 2
    assert all("upic" in url for url in work.image_groups[0])
    assert all("uhead" not in url for url in work.image_groups[0])
    assert work.cover_url == work.image_groups[0][0]


def test_single_picture_detection_helper():
    assert _is_single_picture("", "SINGLE_PICTURE") is True
    assert _is_single_picture('{"singlePicture":true}', "") is True
    assert _is_single_picture('{"singlePicture": true}', "") is True
    assert _is_single_picture("", "VIDEO") is False
    assert _is_single_picture('{"singlePicture":false}', "") is False


@pytest.mark.parametrize("notice", ["该作品已被删除", "作品已删除", "作品不存在"])
def test_deleted_single_picture_still_reports_unavailable(notice):
    """已删除优先于单图兜底：不能把失效作品当成一张封面图返回。"""
    html = (
        '<html><body>"photoType":"SINGLE_PICTURE"'
        '"coverUrls":[{"url":"https://p2.a.yximgs.com/upic/a.jpg"}]'
        f"{notice}</body></html>"
    )
    with pytest.raises(ParseError) as excinfo:
        _parse_html(html)
    assert excinfo.value.code == "WORK_UNAVAILABLE"


def test_unknown_type_without_media_still_fails():
    """不能因为页面上有图片就一律当图文 —— 否则视频解析失败会被掩盖。"""
    html = '<html><body>"photoType":"VIDEO","mainMvUrls":[]</body></html>'
    with pytest.raises(ParseError) as excinfo:
        _parse_html(html)
    assert excinfo.value.code == "PARSE_FAILED"
