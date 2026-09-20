"""快手主页链接识别（PROFILE_LINK）。

背景：真实用户粘贴的快手极速版分享口令（v.kuaishou.com 短链）落点不是作品页，
302 跳到 m 站作者主页 /fw/user/<encId>。主页是 SPA，作品列表接口（/rest/wd/feed/profile、
/rest/wd/user/profile）需要客户端 sig4 加签，桌面版 graphql 也要过验证码 —— 服务端
拿不到单一作品。解析器此前按作品页解析，只能笼统报 PARSE_FAILED。

现在按「落地 URL」提前识别主页，返回 PROFILE_LINK + 可执行引导：
请在主页点开具体作品，再分享该作品。失败样本定性为 input（输入形态问题，
不是解析器 bug，也不是风控），便于统计这类输入的量。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.errors import ParseError
from app.parsers.kuaishou import _is_profile_url, parse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kuaishou"

# 与真实页面同构：主页 302 链上的两种 URL 形态
SHORT_LINK = "https://v.kuaishou.com/KjKexWdn"
PROFILE_M = "https://kpfshanghai.m.chenzhongtech.com/fw/user/3xtmewgdvi4prmi?fid=904397197&cc=share_copylink"
PROFILE_WWW = "https://www.kuaishou.com/profile/3xtmewgdvi4prmi"


# ---------------------------------------------------------------- URL 判定
@pytest.mark.parametrize(
    "url",
    [
        PROFILE_M,  # m 站主页（带子域 + query）
        "https://m.chenzhongtech.com/fw/user/3xtmewgdvi4prmi",
        PROFILE_WWW,  # www 站主页
        "https://www.kuaishou.com/profile/3xtmewgdvi4prmi/",
    ],
)
def test_profile_urls_detected(url):
    assert _is_profile_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        SHORT_LINK,  # 短链本身不是主页（还没跳转）
        "https://www.kuaishou.com/fw/photo/3xabcd",  # 作品页
        "https://m.chenzhongtech.com/fw/photo/3xabcd",
        "https://m.chenzhongtech.com/fw/live/3xabcd",
        "https://www.kuaishou.com/profile/",  # 前缀相同但没有 id，不判主页
        "https://www.kuaishou.com/",  # 站点首页
        "https://v.kuaishou.com/",  # 短链域根路径
        "https://www.douyin.com/profile/3xabcd",  # 别的平台的 profile 路径不归快手管
        "not a url",
        "",
    ],
)
def test_non_profile_urls_not_detected(url):
    assert _is_profile_url(url) is False


# ---------------------------------------------------------------- 端到端
def _transport(profile_page: str) -> httpx.MockTransport:
    """短链 302 → 主页 200 的最小链路。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.kuaishou.com":
            return httpx.Response(302, headers={"location": PROFILE_M})
        return httpx.Response(200, text=profile_page)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_short_link_landing_on_profile_raises_profile_link():
    """极速版口令：短链 302 到 /fw/user/ → PROFILE_LINK，而不是 PARSE_FAILED。"""
    async with httpx.AsyncClient(transport=_transport("<html>profile page</html>")) as c:
        with pytest.raises(ParseError) as excinfo:
            await parse(SHORT_LINK, client=c)
    assert excinfo.value.code == "PROFILE_LINK"
    assert "点开" in excinfo.value.message  # 提示必须可执行


@pytest.mark.asyncio
async def test_direct_profile_url_raises_profile_link():
    """直接粘贴 www 站主页 URL（无跳转）同样识别。"""
    async with httpx.AsyncClient(transport=_transport("<html>profile page</html>")) as c:
        with pytest.raises(ParseError) as excinfo:
            await parse(PROFILE_WWW, client=c)
    assert excinfo.value.code == "PROFILE_LINK"


@pytest.mark.asyncio
async def test_work_page_still_parses():
    """反边界：作品页不受主页判定影响 —— 302 到 /fw/photo/ 仍正常出解析结果。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.kuaishou.com":
            return httpx.Response(302, headers={"location": "https://www.kuaishou.com/fw/photo/3xabcd"})
        return httpx.Response(200, text=(FIXTURES / "video.html").read_text("utf-8"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        work = await parse(SHORT_LINK, client=c)
    assert work.type == "video"
    assert work.video_url


@pytest.mark.asyncio
async def test_profile_check_before_content_length_guard():
    """主页判定先于内容检查：哪怕页面极短也返回 PROFILE_LINK（避免吞进 PARSE_FAILED）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ParseError) as excinfo:
            await parse(PROFILE_WWW, client=c)
    assert excinfo.value.code == "PROFILE_LINK"
