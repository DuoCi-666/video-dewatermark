import pytest
from fastapi.testclient import TestClient

from app.errors import UNSUPPORTED_PLATFORM, ParseError
from app.main import app
from app.parsers.extract import extract_share_url
from app.parsers.xhs import (
    _display_title,
    _extract_initial_state,
    _image_groups,
    _pick_note,
    _video_urls,
)

client = TestClient(app)

STATE_SAMPLE = (
    '<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"abc123":{'
    '"note":{"noteId":"abc123","type":"normal","title":"中二病 x 女仆",'
    '"desc":"#中二病也要谈恋爱[话题]#","user":{"nickname":"团子"},'
    '"imageList":[{"urlDefault":"http://cdn.example/img1_default",'
    '"infoList":[{"url":"http://cdn.example/img1_w480"},{"url":"http://cdn.example/img1_w1080"}]},'
    '{"urlDefault":"http://cdn.example/img2_default","infoList":[]}]}}},'
    '"other":undefined}};</script>'
)

VIDEO_STATE_SAMPLE = (
    '<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"vid9":{'
    '"note":{"noteId":"vid9","type":"video","title":"有只小鹿飞走咯",'
    '"user":{"nickname":"白毛"},'
    '"imageList":[{"urlDefault":"http://cdn.example/cover"}],'
    '"video":{"media":{"stream":{"h264":['
    '{"masterUrl":"http://v4.xhscdn.com/stream/a.mp4"},'
    '{"masterUrl":"http://v6.xhscdn.com/stream/b.mp4"}]}}}}}}}};</script>'
)


def test_extract_share_url_xhs():
    assert extract_share_url("去 https://xhslink.cn/o/1vwajZnuFZ7 看看")[0] == "xiaohongshu"
    assert (
        extract_share_url("https://www.xiaohongshu.com/explore/65abc")[0] == "xiaohongshu"
    )
    with pytest.raises(ParseError) as exc:
        extract_share_url("https://example.com/123/456")
    assert exc.value.code == UNSUPPORTED_PLATFORM.code


def test_extract_initial_state_handles_undefined():
    state = _extract_initial_state(STATE_SAMPLE)
    assert state is not None
    # undefined 字面量被替换为 null；other 位于 note 层
    assert state["note"]["other"] is None
    note = _pick_note(state)
    assert note is not None
    assert note["title"] == "中二病 x 女仆"


def test_image_groups_from_note():
    state = _extract_initial_state(STATE_SAMPLE)
    note = _pick_note(state)
    groups = _image_groups(note)
    assert len(groups) == 2
    assert groups[0][0] == "http://cdn.example/img1_default"
    assert "http://cdn.example/img1_w1080" in groups[0]
    assert groups[1] == ["http://cdn.example/img2_default"]


def test_video_urls_and_title_fallback():
    state = _extract_initial_state(VIDEO_STATE_SAMPLE)
    note = _pick_note(state)
    urls = _video_urls(note)
    assert urls[0] == "http://v4.xhscdn.com/stream/a.mp4"
    assert len(urls) == 2
    assert _display_title(note) == "有只小鹿飞走咯"
    assert _display_title({"title": "", "desc": "一个很长的描述内容abcdefg"}) == "一个很长的描述内容abcdefg"


def test_parse_api_routes_xhs(monkeypatch):
    import app.main as m
    from app.parsers.kuaishou import ParsedWork

    called = {"platform": ""}

    async def fake_xhs(url, timeout=15.0, client=None):
        called["platform"] = "xiaohongshu"
        return ParsedWork(
            type="images",
            title="中二病 x 女仆",
            author="团子",
            image_urls=["http://cdn.example/img1_default"],
            image_groups=[["http://cdn.example/img1_default"]],
        )

    monkeypatch.setattr(m, "parse_xiaohongshu", fake_xhs)
    r = client.post(
        "/api/parse",
        json={"input": "中二病 x 女仆 https://xhslink.cn/o/8gwHIr9JtxG 小红书"},
    )
    body = r.json()
    assert called["platform"] == "xiaohongshu"
    assert body["ok"] is True
    assert body["type"] == "images"
    assert len(body["images"]) == 1
