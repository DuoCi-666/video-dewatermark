class ParseError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        self.status = status
        self.code = code
        self.message = message
        # 供 429/503 使用：提示客户端多久后重试（秒）
        self.retry_after = retry_after
        super().__init__(message)


EMPTY_INPUT = ParseError(400, "EMPTY_INPUT", "请粘贴分享链接")
INVALID_LINK = ParseError(400, "INVALID_LINK", "未识别到有效链接")
UNSUPPORTED_PLATFORM = ParseError(400, "UNSUPPORTED_PLATFORM", "暂支持快手、抖音、小红书、豆包、B站和腾讯频道链接")


def unsupported_platform() -> ParseError:
    """按平台注册表生成提示语，避免新增平台后文案过期。"""
    try:
        from app.parsers.extract import PLATFORMS
    except ImportError:
        return UNSUPPORTED_PLATFORM
    names = "、".join(item.name for item in PLATFORMS)
    if not names:
        return UNSUPPORTED_PLATFORM
    return ParseError(400, "UNSUPPORTED_PLATFORM", f"暂支持{names}链接")
INVALID_INPUT = ParseError(400, "INVALID_INPUT", "请求参数不合法")
DOWNLOAD_FAILED = ParseError(404, "PARSE_FAILED", "下载失败，请重新解析")
NOT_FOUND = ParseError(404, "NOT_FOUND", "接口不存在")
WORK_UNAVAILABLE = ParseError(422, "WORK_UNAVAILABLE", "作品不可用")
# 主页链接：分享口令的落点是「作者主页」而不是具体作品，服务端拿不到单一作品。
# 单独成码：既要给用户可执行的引导，也要在失败样本里与真正的解析失败分开统计。
PROFILE_LINK = ParseError(422, "PROFILE_LINK", "这是主页链接，请进入主页点开想下载的作品，再复制分享该作品")
RATE_LIMITED = ParseError(429, "RATE_LIMITED", "请求太频繁了，请稍后再试", retry_after=10)
INTERNAL_ERROR = ParseError(500, "INTERNAL_ERROR", "服务器内部错误，请稍后重试")
PARSE_FAILED = ParseError(502, "PARSE_FAILED", "解析失败，请稍后重试")
RISK_CONTROL = ParseError(503, "RISK_CONTROL", "平台触发风控验证，请稍后重试")
SERVER_BUSY = ParseError(503, "SERVER_BUSY", "当前请求较多，请稍后重试", retry_after=3)
PARSE_TIMEOUT = ParseError(504, "PARSE_TIMEOUT", "解析超时，请稍后重试")
