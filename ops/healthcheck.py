#!/usr/bin/env python3
"""video-dewatermark 平台解析健康巡检。

对每个平台的真实分享链接调用本地 /api/parse，逐项检查：
  1. HTTP 200 且 ok=true（结构化解析成功）
  2. type 与 links.json 的 expect 一致（expect 为 null 时跳过）
  3. 至少解析出 1 个媒体项（视频看 variants，图文看 images）
  4. 媒体代理 /api/media/{token} 的 Range 请求返回 206/200 且确实有字节

结果写入 ops/health_status.json（最新状态 + 连续失败计数）与 ops/health.log（追加流水）。
任一平台失败 → 退出码 1，并按已配置的渠道推送告警（一个都不配则只写文件）。

环境变量：
  VD_BASE         后端地址，默认 http://127.0.0.1:3001
  VD_LINKS        链接清单，默认 ops/links.json
  VD_LINKS_EXTRA  私有样本清单（可选）：与 VD_LINKS 合并，同名条目私有优先。
                  用于放带分享凭证的链接，避免进公开仓库
  VD_HISTORY      趋势历史文件，默认 ops/health_history.jsonl
  ALERT_DEGRADE   成功率退化时是否告警，默认 1（关掉填 0）
  ALERT_DEGRADE_SILENCE_MINUTES  退化告警静默周期，默认 360（分钟）。
                  同一平台在同一状态期内只提醒一次，避免 20 次窗口内每半小时重发；
                  恢复（率回到阈值以上）后再次变差会重新提醒。填 0 关闭去重。

说明：同一平台配多条样本时，巡检会区分「全部样本失败」（解析器/平台故障，按上表
告警）与「仅部分样本失败」（判定为链接过期，单独走一条"建议更换链接"的提醒），
避免链接过期被当成故障半夜告警。
  瞬时类失败（超时 / 连接被掐）会先「重新解析」重试（默认 3 轮、2s 递增，见
  VD_RETRY_ROUNDS / VD_RETRY_DELAY），规避上游抖动造成的误报；4xx 不重试。
  VD_TIMEOUT      单项超时秒数，默认 60
  VD_RETRY_ROUNDS   瞬时类失败重新解析的最大轮数（不含首次），默认 3
  VD_RETRY_DELAY    重试间隔基数秒数，第 n 轮等 n*该值（2s/4s/6s），默认 2
  ALERT_MIN_FAILURES  连续失败多少次才推送，默认 2（后端不可达则立即推送）
  ALERT_TZ_OFFSET 告警消息里的时间显示时区，默认 8（北京时间）
  SITE_URL        站点地址（可选）：填了会在告警里附上，便于手机端直接打开核对
  ALERT_DIAG      告警里是否附带「统计摘要 + 待修样本」诊断信息，默认 1（关掉填 0）
  ALERT_DIAG_LINES  诊断里最多列几个异常平台 / 几条样本，默认 5
  ALERT_WEBHOOK   群机器人 webhook（企业微信 / 钉钉格式）
  ALERT_EMAIL_TO  邮件告警收件人，多个用英文逗号分隔
  SMTP_HOST       邮件服务器，如 smtp.qq.com
  SMTP_PORT       默认 465（SSL）
  SMTP_USER       发件邮箱；SMTP_PASS 填其授权码（非登录密码）
  VD_ENV_FILE     配置文件路径，默认 /etc/video-dewatermark/health.env
                  （手动运行时也会读取它，行为与 systemd 定时任务一致；
                    已存在的环境变量优先于文件内容）

用法：
  python3 ops/healthcheck.py                # 常规巡检
  python3 ops/healthcheck.py --test-alert   # 只发一条测试告警，验证通道是否通
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 趋势追踪：把「瞬时状态」升级为「成功率趋势」。同目录模块，直接靠 OPS 在
# sys.path 里（脚本方式运行）导入；作为模块导入时退化为相对目录加载。
try:
    import health_trend
except ImportError:  # pragma: no cover - 仅在非常规导入路径下触发
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "health_trend", Path(__file__).resolve().parent / "health_trend.py"
    )
    health_trend = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(health_trend)

ENV_FILE = Path(os.environ.get("VD_ENV_FILE", "/etc/video-dewatermark/health.env"))


def _load_env_file(path: Path) -> None:
    """把配置文件里的 KEY=VALUE 补进环境变量（已存在的环境变量优先）。

    定时任务由 systemd 通过 EnvironmentFile 注入这些变量；手动执行本脚本时
    没有这一步，会出现「手动跑不发告警、定时任务发」的错觉，所以这里自己读一次，
    保证两种运行方式行为一致。
    """
    try:
        text = path.read_text("utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(ENV_FILE)

BASE = os.environ.get("VD_BASE", "http://127.0.0.1:3001").rstrip("/")
OPS = Path(__file__).resolve().parent
LINKS_PATH = Path(os.environ.get("VD_LINKS", OPS / "links.json"))
# 私有样本清单（可选）：仓库里的 links.json 是公共样本，不含任何分享凭证；
# 部署者把自己的链接写在这里，巡检会把它与公共样本**合并**。
# 这样私有链接（带 xsec_token 等分享凭证）不必进公开仓库，也不会被覆盖掉。
LINKS_EXTRA_PATH = Path(os.environ["VD_LINKS_EXTRA"]) if os.environ.get("VD_LINKS_EXTRA") else None
STATUS_PATH = Path(os.environ.get("VD_STATUS", OPS / "health_status.json"))
LOG_PATH = Path(os.environ.get("VD_LOG", OPS / "health.log"))
# 趋势历史：每次巡检追加一行精简记录（平台粒度），用于算成功率趋势
HISTORY_PATH = Path(os.environ.get("VD_HISTORY", OPS / "health_history.jsonl"))
# 退化告警开关：默认开启。它会报「成功率偏低但尚未连续失败」的平台
ALERT_DEGRADE = (os.environ.get("ALERT_DEGRADE", "1") or "1").strip() not in ("0", "false", "no", "")
WEBHOOK = os.environ.get("ALERT_WEBHOOK", "").strip()
# 邮件告警（可选）
ALERT_EMAIL_TO = [x.strip() for x in os.environ.get("ALERT_EMAIL_TO", "").split(",") if x.strip()]
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465") or 465)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
# 连续失败达到该次数才推送告警（避免把 CDN 抖动当故障）；后端不可达时不看该阈值
ALERT_MIN_FAILURES = int(os.environ.get("ALERT_MIN_FAILURES", "2") or 2)
# 退化告警的静默周期（分钟）：同一平台在同一状态期内只提醒一次，避免每半小时重发。
# 默认 6 小时（一个班次最多一封），设 0 关闭去重、恢复为「每轮都报」的老行为。
DEGRADE_SILENCE_MINUTES = int(os.environ.get("ALERT_DEGRADE_SILENCE_MINUTES", "360") or 0)
# 告警时间显示用的时区偏移（小时）。服务器多为 UTC，直接展示会让人先算时区
ALERT_TZ_OFFSET = float(os.environ.get("ALERT_TZ_OFFSET", "8") or 8)
# 告警里带上主机名：多台机器共用同一个告警渠道时，一眼看出是哪台出问题
ALERT_HOSTNAME = os.environ.get("ALERT_HOSTNAME", "").strip() or os.uname().nodename
# 站点地址（可选，默认空 → 不显示；避免把具体域名写进公开仓库）
SITE_URL = os.environ.get("SITE_URL", "").strip()
# 告警附带诊断摘要（统计 + 待修样本），让人收到邮件即可判断「是不是只有我这条挂了」
ALERT_DIAG = (os.environ.get("ALERT_DIAG", "1") or "1").strip() not in ("0", "false", "no", "")
ALERT_DIAG_LINES = int(os.environ.get("ALERT_DIAG_LINES", "5") or 5)
TIMEOUT = float(os.environ.get("VD_TIMEOUT", "60"))
# 瞬时类失败的重试轮数与间隔（见 check_one 注释）。4xx 等确定性失败不重试。
RETRY_ROUNDS = int(os.environ.get("VD_RETRY_ROUNDS", "3") or 3)
RETRY_BASE_DELAY = float(os.environ.get("VD_RETRY_DELAY", "2"))
UA = "video-dewatermark-healthcheck/1.0"
# 标记请求来源，服务端据此把巡检流量与真实用户流量分开统计（见 app/stats.py）
SOURCE = "healthcheck"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _request(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = TIMEOUT):
    """返回 (status, body_bytes)；HTTPError 也算正常返回。"""
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    req.add_header("X-VD-Source", SOURCE)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post_json(url: str, payload: dict) -> tuple[int, dict | None]:
    status, body = _request(url, data=json.dumps(payload).encode("utf-8"))
    try:
        return status, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return status, None


def _get_json(url: str) -> tuple[int, dict | None]:
    status, body = _request(url)
    try:
        return status, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return status, None


def _media_head(url: str) -> tuple[int, int]:
    """对媒体代理发 Range 请求，返回 (status, 读到的字节数)。"""
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", UA)
        req.add_header("Range", "bytes=0-2047")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, len(resp.read(4096))
    except urllib.error.HTTPError as exc:
        return exc.code, 0
    except Exception:
        return 0, 0


def _load_links(path: Path, *, optional: bool = False) -> dict:
    """读取巡检样本清单，返回 {name: spec}（忽略 _ 开头的说明字段）。

    optional=True 时文件不存在不报错（私有清单是可选的），返回空字典。
    解析失败一律报错退出，避免"清单写坏了但巡检静默跳过"这种最坏情况。
    """
    if optional and not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"链接清单读取失败 {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return {k: v for k, v in data.items() if not k.startswith("_")}


# 瞬时类失败（上游网络抖动 / 连接被掐）才值得重试；4xx 是确定性失败，重试无意义。
# PARSE_TIMEOUT 来自应用内的 httpx 超时（app/main.py），本质是上游没在 15s 内回包；
# 「HTTP 0 bytes=0」是媒体代理连不上 CDN。二者都会被复测立刻打回绿色，是典型的抖动窗口。
_TRANSIENT_MARKERS = ("PARSE_TIMEOUT", "TIMEOUT", "HTTP 0 ", "bytes=0", "请求失败")


def _is_transient(detail: str) -> bool:
    """判断失败是否属于「上游瞬时抖动」，只有这类才重试。"""
    if not detail:
        return False
    return any(marker in detail for marker in _TRANSIENT_MARKERS)


def attempt_once(spec: dict) -> dict:
    """跑单轮检查：解析 + 媒体代理校验，返回条目级结果（不做任何重试）。

    失败时只填 detail/ok 等字段，由 check_one() 决定是否重新解析重试。
    """
    url = spec.get("url", "")
    expect = spec.get("expect")
    result = {
        "platform": spec.get("platform", "?"),
        "url": url,
        "expect": expect,
        "ok": False,
        "detail": "",
        "type": None,
        "media": 0,
        "latency_ms": 0,
    }

    started = time.monotonic()
    try:
        status, payload = _post_json(f"{BASE}/api/parse", {"input": url})
    except Exception as exc:  # 服务不可达
        result["detail"] = f"请求失败: {type(exc).__name__}: {exc}"
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result
    result["latency_ms"] = int((time.monotonic() - started) * 1000)

    if status != 200 or not payload or not payload.get("ok"):
        code = (payload or {}).get("code", "")
        message = (payload or {}).get("message", "")
        result["detail"] = f"解析失败 HTTP {status} {code} {message}".strip()
        return result

    kind = payload.get("type")
    result["type"] = kind

    if kind == "video":
        variants = payload.get("variants") or []
        media_url = variants[0].get("mediaUrl") if variants else payload.get("videoUrl")
        count = len(variants) if variants else (1 if payload.get("videoUrl") else 0)
    elif kind == "images":
        images = payload.get("images") or []
        media_url = images[0].get("previewUrl") if images else None
        count = len(images)
    else:
        result["detail"] = f"未知 type={kind!r}"
        return result

    result["media"] = count
    if count <= 0 or not media_url:
        result["detail"] = "未解析出任何媒体链接"
        return result

    if expect and kind != expect:
        result["detail"] = f"类型不符：期望 {expect}，实际 {kind}"
        return result

    # 媒体代理连通性 + Range 支持
    mstatus, nbytes = _media_head(f"{BASE}{media_url}")
    if mstatus not in (200, 206) or nbytes <= 0:
        result["detail"] = f"媒体代理异常 HTTP {mstatus} bytes={nbytes}"
        return result
    result["media_status"] = mstatus
    result["detail"] = f"type={kind} media={count} range={mstatus} {nbytes}B"
    result["ok"] = True
    return result


def check_one(name: str, spec: dict) -> dict:
    """单条样本检查；瞬时类失败时「重新解析」重试，规避上游抖动造成的误报。

    为什么必须是「重新解析」而不是「重试同一个媒体直链」：曾用 1s 内重放同一直链
    来兜底，但抖动窗口常 >1s，链接一旦拿死就会稳定复现同一失败 —— 三次误报
    （miyoushe / zuiyou 的 HTTP 0、huya 的 504 PARSE_TIMEOUT）都是这么漏出去的。
    重新请求 /api/parse 能拿到重新签发的直链，覆盖真正的抖动窗口。

    重试策略：最多 3 轮，间隔 2s、4s 递增；4xx（签名失效 / 资源删除）不重试，
    因为那是确定性失败，重试只会拖长巡检时间、也不会变绿。
    """
    result = None
    for round_no in range(RETRY_ROUNDS + 1):
        result = attempt_once(spec)
        if result["ok"] or not _is_transient(result["detail"]):
            break
        if round_no < RETRY_ROUNDS:
            delay = RETRY_BASE_DELAY * (round_no + 1)
            print(
                f"[retry] {name} 第 {round_no + 1} 轮瞬时失败"
                f"（{result['detail']}），{delay:.0f}s 后重新解析",
                file=sys.stderr,
            )
            time.sleep(delay)
    if not result["ok"] and RETRY_ROUNDS:
        result["retried"] = RETRY_ROUNDS
    return result


def load_previous() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text("utf-8"))
    except Exception:
        return {}


def _alert_time(iso: str) -> str:
    """把 ISO 时间转成告警阅读时区（默认北京时间）并格式化。"""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=ALERT_TZ_OFFSET)))
    return local.strftime("%Y-%m-%d %H:%M") + "（北京时间）"


def _diag_lines() -> list[str]:
    """取「统计摘要 + 待修样本」的精简版，供告警正文附带。

    为什么不在告警里塞完整的 /api/stats/report：那是给模型看的全量报告（含
    表格与全部样本，动辄几 KB），而告警的第一读者是手机上的人 —— 需要的是
    「除了我这条，是不是整体都在挂」。所以这里只保留**异常信号**：

      · 只列成功率 < ALERT_OK_RATE 或确有失败调用的平台（全绿的日期不占行数）
      · 样本只给「定性分布 + 最该修的 import 类前 N 条」，gone（作品已删）不复述

    任何一步出错都返回空列表：诊断是告警的附赠品，不能因为它取不到就丢掉告警本身。
    """
    if not ALERT_DIAG:
        return []
    try:
        status, stats_report = _get_json(f"{BASE}/api/stats?fmt=json")
        if status != 200 or not stats_report:
            return [f"（诊断信息未取到：/api/stats 返回 HTTP {status}）"]
        _, failures_report = _get_json(f"{BASE}/api/stats/failures?fmt=json&limit={ALERT_DIAG_LINES}")
    except Exception as exc:
        return [f"（诊断信息未取到：{type(exc).__name__}: {exc}）"]

    lines: list[str] = []
    overall = stats_report.get("overall") or {}
    rate = overall.get("successRate")
    rate_text = "—" if rate is None else f"{rate * 100:.1f}%"
    lines.append(f"近 {stats_report.get('retentionDays')} 天：全部平台 {overall.get('total', 0)} 次调用，"
                 f"成功率 {rate_text}，失败 {overall.get('fail', 0)} 次")

    # 只挑「确实在出问题」的平台：有失败调用，或成功率低于 95% 且调用量可观
    bad = []
    for platform, m in (stats_report.get("platforms") or {}).items():
        if m.get("fail"):
            bad.append((m.get("fail", 0), platform, m))
    bad.sort(key=lambda item: -item[0])
    for fail, platform, m in bad[:ALERT_DIAG_LINES]:
        p_rate = m.get("successRate")
        p_rate_text = "—" if p_rate is None else f"{p_rate * 100:.1f}%"
        lines.append(f"  · {platform}: 失败 {fail}/{m.get('total', 0)}（成功率 {p_rate_text}）")
    if len(bad) > ALERT_DIAG_LINES:
        lines.append(f"  · …另有 {len(bad) - ALERT_DIAG_LINES} 个平台有失败调用")

    items = (failures_report or {}).get("items") or []
    summary = (failures_report or {}).get("summary") or {}
    by_kind = summary.get("byKind") or {}
    if items or by_kind:
        lines.append("待修样本：" + "　".join(f"{k}={v}" for k, v in by_kind.items()))
        # 跳过 gone（作品已删，无需处理）与 _rejected（用户贴了站外链接，非解析器问题），
        # 按出现次数排序，让最该修的那条排在最前
        shown = [
            item for item in items
            if item.get("kind") not in ("gone", None) and item.get("platform") != "_rejected"
        ]
        shown.sort(key=lambda item: -item.get("count", 0))
        for item in shown[:ALERT_DIAG_LINES]:
            platform = item.get("platform") or "?"
            lines.append(
                f"  · [{item.get('kind')}] {platform} ×{item.get('count')} "
                f"{item.get('lastMessage') or ''}".rstrip()
            )
        lines.append("（完整报告：本机 curl /api/stats/report）")
    return lines


def _alert_message(iso: str, summary: str, hstatus: int, failures: list[dict]) -> str:
    """组装告警正文。

    后端整体不可达时，各平台必然一并失败（原因完全相同），逐条列十几次没有
    信息量，折叠成一句；后端正常、个别平台挂掉时才逐条列出，便于直接定位。
    """
    lines = [
        f"[video-dewatermark] 巡检异常 {_alert_time(iso)}",
        f"{ALERT_HOSTNAME} · {summary}",
    ]
    if SITE_URL:
        lines.append(f"站点：{SITE_URL}")
    if hstatus != 200:
        lines.append(f"后端 /api/health 异常: HTTP {hstatus}")
        if failures:
            lines.append(f"→ 连带 {len(failures)} 个平台失败，原因均为：{failures[0]['detail']}")
        else:
            lines.append("→ 后端不可达，未能取得平台数据")
    else:
        for item in failures:
            lines.append(
                f"· {item['name']}({item['platform']}) 连续失败{item['consecutive_failures']}次: {item['detail']}"
            )
    # 附一段诊断摘要：让收到告警的人不必 SSH 上来就知道「整体是否也在挂、该修哪条」
    diag = _diag_lines()
    if diag:
        lines.append("")
        lines.append("— 诊断摘要 —")
        lines.extend(diag)
    return "\n".join(lines)


def _alert_webhook(message: str) -> str:
    """群机器人（企业微信 / 钉钉格式）。"""
    payload = json.dumps({"msgtype": "text", "text": {"content": message}}).encode("utf-8")
    status, _ = _request(WEBHOOK, data=payload, timeout=10)
    if status >= 400:
        raise RuntimeError(f"webhook 返回 HTTP {status}")
    return f"HTTP {status}"


def _alert_email(subject: str, message: str) -> str:
    """SMTP 发信（默认 465 SSL，QQ 邮箱用授权码登录）。"""
    import smtplib
    from email.message import EmailMessage

    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = SMTP_USER
    mail["To"] = ", ".join(ALERT_EMAIL_TO)
    mail.set_content(message)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(mail)
    return "已发往 " + ", ".join(ALERT_EMAIL_TO)


def alert(subject: str, message: str) -> dict:
    """按已配置的渠道推送告警。

    返回各渠道结果（"ok: ..." / "failed: ..."），并写进巡检状态文件 —— 这样
    「告警没发出去」本身也是可见的，不会出现「以为有告警其实静默失败」。
    """
    outcome: dict[str, str] = {}
    if WEBHOOK:
        try:
            outcome["webhook"] = "ok: " + _alert_webhook(message)
        except Exception as exc:
            outcome["webhook"] = f"failed: {exc}"
    if ALERT_EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            outcome["email"] = "ok: " + _alert_email(subject, message)
        except Exception as exc:
            outcome["email"] = f"failed: {exc}"
    if not outcome:
        print("[alert] 未配置告警渠道（仅写文件）")
        return outcome
    for channel, result in outcome.items():
        print(f"[alert] {channel} -> {result}")
        if result.startswith("failed"):
            print(f"[alert] ⚠ {channel} 告警未能送达，请检查配置", file=sys.stderr)
    return outcome


def main() -> int:
    if "--test-alert" in sys.argv:
        message = "这是一条测试告警（来自 {node}，{when}）。收到即说明告警链路正常。".format(
            node=os.uname().nodename, when=_alert_time(_now())
        )
        diag = _diag_lines()
        if diag:
            message += "\n\n— 诊断摘要 —\n" + "\n".join(diag)
        result = alert("[video-dewatermark] 告警通道测试", message)
        return 0 if result and all(v.startswith("ok") for v in result.values()) else 1

    if not LINKS_PATH.exists():
        print(f"链接清单不存在: {LINKS_PATH}", file=sys.stderr)
        return 2
    links = _load_links(LINKS_PATH)
    if LINKS_EXTRA_PATH is not None:
        extra = _load_links(LINKS_EXTRA_PATH, optional=True)
        # 同名条目：私有清单优先（便于部署者替换掉公共样本里已失效的那条）
        links.update(extra)
        if extra:
            print(f"[links] 合并私有样本 {len(extra)} 条（{LINKS_EXTRA_PATH}）")
    previous = load_previous()
    prev_platforms = previous.get("platforms", {})

    # 服务存活
    try:
        hstatus, hpayload = _get_json(f"{BASE}/api/health")
    except Exception as exc:
        hstatus, hpayload = 0, None
        print(f"后端不可达: {exc}", file=sys.stderr)

    results = {}
    for name, spec in links.items():
        item = check_one(name, spec)
        item["name"] = name
        prev = prev_platforms.get(name, {})
        item["consecutive_failures"] = 0 if item["ok"] else int(prev.get("consecutive_failures", 0)) + 1
        results[name] = item

    failures = [v for v in results.values() if not v["ok"]]
    overall = hstatus == 200 and not failures

    ok_count = sum(1 for v in results.values() if v["ok"])

    # 趋势：加载历史 → 追加本次 → 算成功率与退化平台。
    # 顺序很重要：先算趋势（不含本次，避免本次结果既进窗口又被引用两次）；
    # 追加放最后，保证报告里的 trend 与磁盘历史口径一致。
    history = health_trend.load_history(HISTORY_PATH)
    trends = health_trend.platform_trends(history)
    degraded = health_trend.degraded_platforms(trends) if ALERT_DEGRADE else []

    # 退化告警去重：同一平台在同一个「状态期」内只提醒一次。
    # 不做的话，窗口（20 次 ≈ 10 小时）内的间歇失败会让每轮巡检都重发一封
    # 一样的邮件（2026-09-19 huya 504 抖动从早 7 点连发到中午）。这里按上一轮
    # 落盘的状态快照判定「首次 / 持续静默 / 超时再提醒」，恢复后自然重置。
    degraded_prev = previous.get("degradedState") or {}
    if ALERT_DEGRADE and DEGRADE_SILENCE_MINUTES > 0:
        degraded, degraded_state = health_trend.split_degraded_alerts(
            degraded, degraded_prev, now=_now(), silence_minutes=DEGRADE_SILENCE_MINUTES
        )
    else:
        degraded_state = degraded_prev

    report = {
        "checked_at": _now(),
        "base": BASE,
        "ok": overall,
        "backend": {"status": hstatus, "payload": hpayload},
        "summary": f"{ok_count}/{len(results)} 平台正常",
        "platforms": results,
        "failures": [v["name"] for v in failures],
        "trend": health_trend.build_trend_section(trends, degraded),
        # 退化状态快照：供下一轮判定「是否还在同一个状态期」用（见上方去重逻辑）
        "degradedState": degraded_state,
        # 多样本判定：区分「解析器坏了」与「这条链接过期了」
        "sampleCoverage": health_trend.classify_failures(results),
    }

    # 控制台表格
    print(f"[{report['checked_at']}] {report['summary']}  backend=HTTP {hstatus}")
    for name, item in results.items():
        mark = "OK  " if item["ok"] else "FAIL"
        extra = "" if item["ok"] else f"  连续失败 {item['consecutive_failures']} 次"
        print(f"  {mark} {name:<18} {item['latency_ms']:>6}ms  {item['detail']}{extra}")

    for line in health_trend.render_trend_lines(report):
        print(line)
    for line in health_trend.render_multisample_lines(report):
        print(line)

    # 区分「真故障」「样本链接过期」「上游瞬时抖动」：
    # 某个平台的多条样本全军覆没才算故障；只是部分样本失败时，按失败原因细分 ——
    # 含 4xx 的判为链接过期（提示更换），纯超时/连接层的判为上游瞬时抖动（只提示观察，
    # 明确不要换链接）。没必要按故障告警把人叫起来。单样本平台失败归入 allDown（保守，宁可报）。
    coverage = report["sampleCoverage"]
    all_down = coverage["allDown"]
    partial_only = bool(failures) and not all_down

    if not overall:
        worst = max((int(item["consecutive_failures"]) for item in failures), default=0)
        if partial_only:
            flaky_n = len(coverage.get("flaky") or [])
            expired_n = len(coverage.get("partial") or [])
            if flaky_n and not expired_n:
                headline = "本次巡检有样本失败，失败原因均为连接/超时层，判定为上游瞬时抖动（非解析器故障，无需更换链接）："
            elif expired_n and not flaky_n:
                headline = "本次巡检有样本失败，判定为部分样本链接已过期（非解析器故障，建议更换链接）："
            else:
                headline = "本次巡检有样本失败，已按原因区分「链接过期（建议更换）」与「上游瞬时抖动（建议观察，勿换链接）」："
            report["alerts"] = alert(
                f"[video-dewatermark] 巡检样本部分失效 {_alert_time(report['checked_at'])}",
                "\n".join(
                    [
                        headline,
                        *health_trend.render_multisample_lines(report),
                    ]
                ),
            )
        elif hstatus != 200 or worst >= ALERT_MIN_FAILURES:
            report["alerts"] = alert(
                f"[video-dewatermark] 巡检异常 {_alert_time(report['checked_at'])}",
                _alert_message(report["checked_at"], report["summary"], hstatus, failures),
            )
        else:
            # 首次失败多为 CDN / 链接抖动（见 README 巡检说明），先不打扰
            names = ", ".join(item["name"] for item in failures)
            print(
                f"[alert] 首次失败（最高连续 {worst} 次 < {ALERT_MIN_FAILURES}）：{names}"
                f" —— 按抖动处理，本次不推送"
            )
    elif degraded:
        # 本次全绿但有平台在缓慢劣化：这类最容易漏（当前成功、历史成功率低），
        # 提前提示，让维护者有时间在它彻底挂掉前介入。
        trend_lines = health_trend.render_trend_lines(report)
        report["alerts"] = alert(
            f"[video-dewatermark] 解析成功率退化 {_alert_time(report['checked_at'])}",
            "\n".join(["本次巡检全部通过，但检出平台成功率下降：", *trend_lines]),
        )
    elif report["trend"]["degraded"] and not degraded:
        # 上面已由去重过滤掉（同一状态期内提醒过）→ 只落日志不推送，
        # 避免 20 次窗口内每半小时重发一封一样的邮件。
        names = ", ".join(item["name"] for item in report["trend"]["degraded"])
        print(f"[alert] 平台仍在退化但处于静默期（{DEGRADE_SILENCE_MINUTES} 分钟内已提醒）"
              f"：{names} —— 本次不推送")

    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    # 追加精简历史（供下次算趋势）。放在告警之后：告警文案用的是「本次之前」的
    # 趋势窗口，不会被本次结果污染。
    health_trend.append_history(HISTORY_PATH, report["checked_at"], results)
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps(report, ensure_ascii=False) + "\n")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
