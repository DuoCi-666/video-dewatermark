from app.errors import EMPTY_INPUT, INVALID_LINK, UNSUPPORTED_PLATFORM, ParseError
from app.parsers.extract import extract_kuaishou_url, split_link_segments
from app.parsers.kuaishou import _parse_html
from app.parsers.naming import filename_from_title


def test_extract_from_share_token():
    text = (
        'https://v.kuaishou.com/7ERo1PvZ 以后就是最好的朋友"王者荣耀 '
        "该作品在快手被播放过16.9万次，点击链接，打开【快手极速版】直接观看！"
    )
    assert extract_kuaishou_url(text) == "https://v.kuaishou.com/7ERo1PvZ"


def test_empty_input():
    try:
        extract_kuaishou_url("   ")
        raise AssertionError("should fail")
    except ParseError as exc:
        assert exc.code == EMPTY_INPUT.code


def test_invalid_link():
    try:
        extract_kuaishou_url("没有链接的口令文本")
        raise AssertionError("should fail")
    except ParseError as exc:
        assert exc.code == INVALID_LINK.code


def test_unsupported_platform():
    try:
        extract_kuaishou_url("https://www.douyin.com/video/123")
        raise AssertionError("should fail")
    except ParseError as exc:
        assert exc.code == UNSUPPORTED_PLATFORM.code


def test_filename_sanitize():
    name = filename_from_title('以后就是最好的朋友#王者荣耀/:\"', "mp4")
    assert "/" not in name
    assert name.endswith(".mp4")


def test_filename_fallback_when_title_empty():
    """标题为空时用 fallback（原先散落在各解析器里，现在统一到 naming.py）。"""
    assert filename_from_title("", "mp4") == "video.mp4"
    assert filename_from_title("   ", "jpg", fallback="cover") == "cover.jpg"


def test_filename_truncates_long_title():
    name = filename_from_title("长" * 80, "mp4")
    assert name == "长" * 40 + ".mp4"


from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "kuaishou"


def test_parse_video_fixture():
    html = (FIXTURES / "video.html").read_text(encoding="utf-8")
    work = _parse_html(html)
    assert work.type == "video"
    assert work.video_url and ".mp4" in work.video_url
    assert "朋友" in work.title or work.title


def test_parse_images_fixture():
    html = (FIXTURES / "images.html").read_text(encoding="utf-8")
    work = _parse_html(html)
    assert work.type == "images"
    assert len(work.image_urls) >= 2
    assert all(u.startswith("https://") for u in work.image_urls)


# ---- split_link_segments：批量解析的口令拆分 ----


def test_split_link_segments_multiline_token_counts_as_one():
    """小红书口令复制出来是两行、只有一行带链接 → 应拆成 1 条而不是 2 条。"""
    text = (
        "快来看 扫地僧 创作的故事《老板的英语补习》！1050+人点赞过这个作品，"
        " https://xhslink.cn/o/8R15HScfQYh\n"
        "存下口令，来【小红书】瞧瞧这篇~"
    )
    segments = split_link_segments(text)
    assert len(segments) == 1
    assert "xhslink.cn/o/8R15HScfQYh" in segments[0]


def test_split_link_segments_leading_text_joins_link():
    """链接前的口令文本要跟着链接走，不单独成条。"""
    text = "【小红书】\nhttps://xhslink.cn/o/abc\n存下口令，来【小红书】瞧瞧这篇~"
    segments = split_link_segments(text)
    assert len(segments) == 1
    assert segments[0].startswith("【小红书】")
    assert "xhslink.cn/o/abc" in segments[0]


def test_split_link_segments_two_links():
    text = "看这个 https://xhslink.cn/o/aaa\n第二个 https://xhslink.cn/o/bbb\n完了"
    segments = split_link_segments(text)
    assert len(segments) == 2
    assert "xhslink.cn/o/aaa" in segments[0]
    assert "xhslink.cn/o/bbb" in segments[1]


def test_split_link_segments_no_url_keeps_text():
    """整段没有链接：原样返回，让调用方给出明确报错而不是静默吞掉。"""
    text = "纯文本没有链接\n第二行"
    segments = split_link_segments(text)
    assert len(segments) == 1


def test_split_link_segments_empty():
    assert split_link_segments("") == []
    assert split_link_segments("  \n \t ") == []
