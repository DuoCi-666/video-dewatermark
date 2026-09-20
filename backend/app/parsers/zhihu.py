"""知乎视频解析器。

知乎 zvideo API：`GET https://www.zhihu.com/api/v4/zvideos/{video_id}`
直接返回 JSON，包含标题、作者、多清晰度直链（ld/sd/hd/fhd）。
无需 Cookie，无需解析 HTML。

典型链接：
  https://www.zhihu.com/zvideo/1342930761977176064
  https://www.zhihu.com/question/123456789/answer/987654321（含视频的回答）
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from app.errors import PARSE_FAILED, PARSE_TIMEOUT, WORK_UNAVAILABLE, ParseError
from app.parsers.kuaishou import ParsedWork, VideoVariant

ZHIHU_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ZVIDEO_API = "https://www.zhihu.com/api/v4/zvideos/{video_id}"

# URL 中的 zvideo ID
ZVIDEO_RE = re.compile(r"/zvideo/(\d+)")


def _extract_zvideo_id(url: str) -> str | None:
    """从 URL 提取 zvideo ID。"""
    match = ZVIDEO_RE.search(urlparse(url).path or "")
    return match.group(1) if match else None


def _quality_rank(format_id: str) -> int:
    """清晰度排序权重（越高越清晰）。"""
    return {"ld": 1, "sd": 2, "hd": 3, "fhd": 4}.get(format_id, 0)


async def parse(
    url: str,
    *,
    timeout: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> ParsedWork:
    """解析知乎视频链接。"""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": ZHIHU_UA},
            timeout=httpx.Timeout(timeout),
        )

    try:
        video_id = _extract_zvideo_id(url)
        if not video_id:
            raise PARSE_FAILED

        api_url = ZVIDEO_API.format(video_id=video_id)
        resp = await client.get(api_url)
        if resp.status_code == 404:
            raise WORK_UNAVAILABLE
        resp.raise_for_status()
        data = resp.json()

        title = data.get("title") or ""
        author_info = data.get("author") or {}
        author = author_info.get("name") or ""

        video = data.get("video") or {}
        thumbnail = video.get("thumbnail") or data.get("image_url")
        playlist = video.get("playlist") or {}

        variants: list[VideoVariant] = []
        for format_id, quality in playlist.items():
            play_url = quality.get("url") or quality.get("play_url")
            if not play_url:
                continue
            variants.append(VideoVariant(
                label=format_id.upper(),
                rank=_quality_rank(format_id),
                urls=[play_url],
            ))
        variants.sort(key=lambda v: v.rank, reverse=True)

        if not variants:
            raise PARSE_FAILED

        return ParsedWork(
            type="video",
            title=title.strip(),
            author=author.strip(),
            cover_url=thumbnail,
            video_url=variants[0].urls[0],
            variants=variants,
        )

    except ParseError:
        raise
    except httpx.TimeoutException as exc:
        raise PARSE_TIMEOUT from exc
    except Exception as exc:
        raise PARSE_FAILED from exc
    finally:
        if own_client and client:
            await client.aclose()