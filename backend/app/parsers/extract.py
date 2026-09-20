import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.errors import EMPTY_INPUT, INVALID_LINK, unsupported_platform

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

KUAISHOU_HOSTS = (
    "kuaishou.com",
    "kuaishouapp.com",
    "gifshow.com",
    "chenzhongtech.com",
    "kwai.com",
    "kwaicdn.com",
    "yximgs.com",
    "kwimgs.com",
)

DOUYIN_HOSTS = (
    "douyin.com",
    "iesdouyin.com",
)

XHS_HOSTS = (
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
)

DOUBAO_HOSTS = (
    "doubao.com",
)

BILIBILI_HOSTS = (
    "bilibili.com",
    "b23.tv",
    "bilibili.tv",
)

QQCHANNEL_HOSTS = ("pd.qq.com",)

MIYOUSHE_HOSTS = (
    "miyoushe.com",
    "mihoyo.com",
)

WEIBO_HOSTS = (
    "weibo.com",
    "weibo.cn",
    "m.weibo.cn",
)

JIMENG_HOSTS = (
    "jimeng.jianying.com",
    "jimeng.com",
)

PIPIX_HOSTS = (
    "pipix.com",
)

PIPIGAOXIAO_HOSTS = (
    "ippzone.com",
)

ZUIYOU_HOSTS = (
    "xiaochuankeji.cn",
)

WEISHI_HOSTS = (
    "weishi.qq.com",
)

TOUTIAO_HOSTS = (
    "toutiao.com",
)

CCTV_HOSTS = (
    "cctv.com",
    "cctv.cn",
    "cntv.cn",
)

ACFUN_HOSTS = (
    "acfun.cn",
)

QQVIDEO_HOSTS = (
    "v.qq.com",
)

SOHU_HOSTS = (
    "sohu.com",
)

LISHIPIN_HOSTS = (
    "pearvideo.com",
)

HUYA_HOSTS = (
    "huya.com",
)

ZHIHU_HOSTS = (
    "zhihu.com",
    "zhimg.com",
)

HAOKAN_HOSTS = (
    "haokan.baidu.com",
    "haokan.hao123.com",
    "sv.baidu.com",
)

WEIXIN_HOSTS = (
    "mp.weixin.qq.com",
    "weixin.qq.com",
)


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    hosts: tuple[str, ...]
    kinds: tuple[str, ...] = ("video", "images")
    # 媒体直链带短时效签名：代理拿到全部候选都失败时，需要重新解析换新地址
    signed_media: bool = False


# 平台清单的**唯一事实来源**：识别、路由与前端展示都从这里取。
# 新增平台只改这一处（加上对应的 parser），前端会自动跟上，不会出现
# "后端已支持但界面没显示"的不一致。
PLATFORMS: tuple[Platform, ...] = (
    Platform("kuaishou", "快手", KUAISHOU_HOSTS),
    Platform("douyin", "抖音", DOUYIN_HOSTS),
    Platform("xiaohongshu", "小红书", XHS_HOSTS),
    Platform("doubao", "豆包", DOUBAO_HOSTS),
    Platform("bilibili", "B站", BILIBILI_HOSTS),
    Platform("qqchannel", "腾讯频道", QQCHANNEL_HOSTS, signed_media=True),
    Platform("miyoushe", "米游社", MIYOUSHE_HOSTS, signed_media=True),
    Platform("weibo", "微博", WEIBO_HOSTS, signed_media=True),
    Platform("jimeng", "即梦AI", JIMENG_HOSTS, signed_media=True),
    Platform("pipix", "皮皮虾", PIPIX_HOSTS, signed_media=True),
    Platform("pipigaoxiao", "皮皮搞笑", PIPIGAOXIAO_HOSTS),
    Platform("zuiyou", "最右", ZUIYOU_HOSTS, signed_media=True),
    Platform("weishi", "微视", WEISHI_HOSTS, kinds=("video",), signed_media=True),
    Platform("toutiao", "今日头条", TOUTIAO_HOSTS, kinds=("images",), signed_media=True),
    Platform("cctv", "央视网", CCTV_HOSTS, kinds=("video",)),
    Platform("acfun", "A站", ACFUN_HOSTS, kinds=("video",), signed_media=True),
    Platform("qqvideo", "腾讯视频", QQVIDEO_HOSTS, kinds=("video",), signed_media=True),
    Platform("sohu", "搜狐视频", SOHU_HOSTS, kinds=("video",)),
    Platform("lishipin", "梨视频", LISHIPIN_HOSTS, kinds=("video",)),
    Platform("huya", "虎牙", HUYA_HOSTS, kinds=("video",)),
    Platform("zhihu", "知乎", ZHIHU_HOSTS, kinds=("video",)),
    Platform("haokan", "好看视频", HAOKAN_HOSTS, kinds=("video",), signed_media=True),
    Platform("weixin", "微信公众号", WEIXIN_HOSTS, kinds=("images",)),
)


def signed_media_platforms() -> frozenset[str]:
    """媒体直链带短时效签名的平台（媒体代理据此决定"失败后要不要刷新重试"）。"""
    return frozenset(item.key for item in PLATFORMS if item.signed_media)


SIGNED_MEDIA_PLATFORMS = signed_media_platforms()


def platform_list() -> list[dict]:
    """给前端的平台清单（顺序即展示顺序）。"""
    return [
        {"key": item.key, "name": item.name, "hosts": list(item.hosts), "kinds": list(item.kinds)}
        for item in PLATFORMS
    ]


def extract_first_url(text: str) -> str:
    if text is None:
        raise EMPTY_INPUT
    raw = text.strip()
    if not raw:
        raise EMPTY_INPUT
    match = URL_RE.search(raw)
    if not match:
        raise INVALID_LINK
    url = match.group(0).rstrip(").,，。；;！!?？\"'")
    return url


def _host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def is_kuaishou_url(url: str) -> bool:
    return _matches(_host_of(url), KUAISHOU_HOSTS)


def is_douyin_url(url: str) -> bool:
    return _matches(_host_of(url), DOUYIN_HOSTS)


def is_xhs_url(url: str) -> bool:
    return _matches(_host_of(url), XHS_HOSTS)


def is_doubao_url(url: str) -> bool:
    return _matches(_host_of(url), DOUBAO_HOSTS)


def is_bilibili_url(url: str) -> bool:
    return _matches(_host_of(url), BILIBILI_HOSTS)


def is_qqchannel_url(url: str) -> bool:
    return _matches(_host_of(url), QQCHANNEL_HOSTS)


def is_miyoushe_url(url: str) -> bool:
    return _matches(_host_of(url), MIYOUSHE_HOSTS)


def is_weibo_url(url: str) -> bool:
    return _matches(_host_of(url), WEIBO_HOSTS)


def is_jimeng_url(url: str) -> bool:
    return _matches(_host_of(url), JIMENG_HOSTS)


def is_pipix_url(url: str) -> bool:
    return _matches(_host_of(url), PIPIX_HOSTS)


def is_pipigaoxiao_url(url: str) -> bool:
    return _matches(_host_of(url), PIPIGAOXIAO_HOSTS)


def is_zuiyou_url(url: str) -> bool:
    return _matches(_host_of(url), ZUIYOU_HOSTS)


def is_weishi_url(url: str) -> bool:
    return _matches(_host_of(url), WEISHI_HOSTS)


def is_toutiao_url(url: str) -> bool:
    return _matches(_host_of(url), TOUTIAO_HOSTS)


def is_cctv_url(url: str) -> bool:
    return _matches(_host_of(url), CCTV_HOSTS)


def is_acfun_url(url: str) -> bool:
    return _matches(_host_of(url), ACFUN_HOSTS)


def is_qqvideo_url(url: str) -> bool:
    return _matches(_host_of(url), QQVIDEO_HOSTS)


def is_sohu_url(url: str) -> bool:
    return _matches(_host_of(url), SOHU_HOSTS)


def is_lishipin_url(url: str) -> bool:
    return _matches(_host_of(url), LISHIPIN_HOSTS)


def is_huya_url(url: str) -> bool:
    return _matches(_host_of(url), HUYA_HOSTS)


def is_zhihu_url(url: str) -> bool:
    return _matches(_host_of(url), ZHIHU_HOSTS)


def is_haokan_url(url: str) -> bool:
    return _matches(_host_of(url), HAOKAN_HOSTS)


def is_weixin_url(url: str) -> bool:
    return _matches(_host_of(url), WEIXIN_HOSTS)


def extract_kuaishou_url(text: str) -> str:
    url = extract_first_url(text)
    if not is_kuaishou_url(url):
        raise unsupported_platform()
    return url


def extract_share_url(text: str) -> tuple[str, str]:
    """从分享文案提取链接并识别平台，返回 (platform, url)。"""
    url = extract_first_url(text)
    host = _host_of(url)
    for platform in PLATFORMS:
        if _matches(host, platform.hosts):
            return platform.key, url
    raise unsupported_platform()


def split_link_segments(text: str) -> list[str]:
    """把整段分享文案按「链接边界」拆成若干条（供批量解析使用）。

    分享口令常被 App 换行截断（典型：小红书复制出来是两行、只有一行带链接），
    批量解析若按行拆分，会把"没有链接的续行"当成独立条目去解析并报
    「未识别到有效链接」，体验上像「一条口令算成两条」。这里改为：

    - 整段没有任何 URL → 原样返回单条（让调用方给出明确报错，不静默吞掉）；
    - 有 URL → 「遇到带链接的行即收口成一条」，其前后的纯文本行只作为上下文
      跟随到最近的一条，**不会单独成条**（口令的尾巴如「存下口令，来【小红书】
      瞧瞧这篇~」会被自然并入/丢弃）。
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []
    if not any(URL_RE.search(line) for line in lines):
        return [text.strip()]

    segments: list[str] = []
    current: list[str] = []
    for line in lines:
        if URL_RE.search(line):
            current.append(line)
            segments.append("\n".join(current))
            current = []
        else:
            # 纯文本行：跟随到下一条链接，作为该条口令的上下文
            current.append(line)
    return segments
