#!/usr/bin/env bash
# 安装/更新健康巡检的 systemd timer（幂等，可重复执行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/etc/systemd/system
ENV_DIR=/etc/video-dewatermark

if [ "$(id -u)" != "0" ]; then
  echo "需要 root 权限运行"; exit 1
fi

install -m 644 "$ROOT/ops/systemd/video-dewatermark-health.service" "$DEST/"
install -m 644 "$ROOT/ops/systemd/video-dewatermark-health.timer" "$DEST/"

mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_DIR/health.env" ]; then
  cat > "$ENV_DIR/health.env" <<'EOF'
# video-dewatermark 健康巡检配置（告警渠道可同时配多个；都不配则只写文件）
# 群机器人（企业微信 / 钉钉）：留空则不推送
# ALERT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key
# 邮件告警（QQ 邮箱示例：SMTP_PASS 填“授权码”，不是登录密码）：
# ALERT_EMAIL_TO=you@qq.com
# SMTP_HOST=smtp.qq.com
# SMTP_PORT=465
# SMTP_USER=you@qq.com
# SMTP_PASS=你的授权码
# 其它可调项：
# VD_TIMEOUT=60
# ALERT_MIN_FAILURES=2   # 连续失败几次才推送（默认 2；后端不可达时立即推送）
# ALERT_TZ_OFFSET=8      # 告警时间显示时区（默认北京时间）
EOF
  echo "已生成 $ENV_DIR/health.env（可填入 ALERT_WEBHOOK 开启推送告警）"
else
  echo "$ENV_DIR/health.env 已存在，保留不动"
fi

systemctl daemon-reload
systemctl enable --now video-dewatermark-health.timer
echo
systemctl list-timers video-dewatermark-health.timer --no-pager || true
