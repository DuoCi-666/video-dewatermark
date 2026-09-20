#!/usr/bin/env bash
# video-dewatermark 一键部署
#
#   拉取最新代码 → 安装依赖 → 构建前端 → 重启后端 → 健康检查
#   任一步骤失败 → 自动回滚到部署前的 commit 并重建，避免站点长时间不可用
#
# 用法：
#   ./deploy.sh               常规部署（有更新才构建）
#   ./deploy.sh --force       强制重部署（即使 commit 没变）
#   ./deploy.sh --deep        部署后额外跑一次全平台解析巡检
#
# 环境变量（一般无需改）：
#   APP_DIR=...  PY=...  SERVICE=...  BRANCH=master
#   NPM_CMD="npm ci"  HEALTH_WAIT=40  （无 package-lock.json 时脚本会自动改用 npm install）
set -uo pipefail

APP_DIR="${APP_DIR:-/opt/video-dewatermark}"
PY="${PY:-/opt/miniconda3/bin/python}"
SERVICE="${SERVICE:-video-dewatermark}"
BRANCH="${BRANCH:-master}"
BACKEND_HEALTH="${BACKEND_HEALTH:-http://127.0.0.1:3001/api/health}"
NGINX_BASE="${NGINX_BASE:-http://127.0.0.1:8080}"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/conf.d/video-dewatermark.conf}"
HEALTH_WAIT="${HEALTH_WAIT:-40}"
FORCE=0
DEEP=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --deep) DEEP=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

LOG_DIR="$APP_DIR/ops/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
ln -sfn "$LOG_FILE" "$LOG_DIR/latest.log"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"; }

run_step() {  # run_step <名称> <命令...>
  local name="$1"; shift
  log "▶ $name"
  if "$@" >>"$LOG_FILE" 2>&1; then
    log "  ✔ $name"
    return 0
  fi
  log "  ✘ $name 失败（日志：$LOG_FILE）"
  echo "----- 失败步骤输出（末 30 行）-----" | tee -a "$LOG_FILE"
  tail -n 30 "$LOG_FILE" | sed 's/^/    /'
  echo "----------------------------------"
  return 1
}

build_frontend() {
  local npm_cmd="${NPM_CMD:-}"
  if [ -z "$npm_cmd" ]; then
    if [ -f frontend/package-lock.json ]; then npm_cmd="npm ci"; else npm_cmd="npm install"; fi
  fi
  pushd frontend >/dev/null || return 1
  run_step "安装前端依赖（$npm_cmd）" $npm_cmd &&
    run_step "构建前端" npm run build
  local rc=$?
  popd >/dev/null || return 1
  # 校验产物确实生成
  if [ $rc -eq 0 ] && [ ! -f frontend/dist/index.html ]; then
    log "  ✘ 构建后 frontend/dist/index.html 不存在"
    return 1
  fi
  return $rc
}

restart_backend() {
  run_step "重启后端服务（$SERVICE）" systemctl restart "$SERVICE"
}

wait_health() {  # 最多等 HEALTH_WAIT 秒
  local waited=0
  while [ $waited -lt "$HEALTH_WAIT" ]; do
    if curl -fsS -m 3 "$BACKEND_HEALTH" 2>/dev/null | grep -q '"ok":true'; then
      log "  ✔ 后端健康检查通过（${waited}s）"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  log "  ✘ 后端健康检查超时（${waited}s）：$BACKEND_HEALTH"
  return 1
}

check_nginx() {
  local api_code index_code
  api_code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$NGINX_BASE/api/health" || echo 000)"
  index_code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$NGINX_BASE/" || echo 000)"
  log "  通过 nginx：/api/health=$api_code  /=$index_code"
  if [ "$api_code" != "200" ] || [ "$index_code" != "200" ]; then
    log "  ✘ nginx 链路异常"
    return 1
  fi
  return 0
}

sync_nginx() {
  # 把仓库里的站点配置同步到 nginx：变更前备份，校验失败自动还原
  local src="$APP_DIR/ops/nginx/video-dewatermark.conf"
  local dst="$NGINX_CONF"
  local bak
  [ -f "$src" ] || return 0
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    log "  ✔ nginx 配置无变化"
    return 0
  fi
  bak="$dst.bak.$(date +%Y%m%d-%H%M%S)"
  if [ -f "$dst" ]; then
    cp "$dst" "$bak" && log "  已备份原配置：$bak"
  fi
  cp "$src" "$dst" || { log "  ✘ 写入 $dst 失败"; return 1; }
  if nginx -t >>"$LOG_FILE" 2>&1; then
    systemctl reload nginx >>"$LOG_FILE" 2>&1 && log "  ✔ nginx 配置已更新并 reload"
    return 0
  fi
  log "  ✘ nginx 配置校验失败："
  nginx -t 2>&1 | tail -3 | sed 's/^/    /'
  if [ -f "$bak" ]; then cp "$bak" "$dst"; else rm -f "$dst"; fi
  systemctl reload nginx >>"$LOG_FILE" 2>&1 || true
  log "  ↺ 已还原原配置"
  return 1
}

rollback() {
  log "⚠ 部署失败，回滚到部署前版本 ${PREV:0:7}"
  cd "$APP_DIR" || exit 1
  log "▶ 回滚代码"
  git reset --hard "$PREV" >>"$LOG_FILE" 2>&1 || log "  ✘ git reset 失败"
  run_step "回滚后安装依赖" "$PY" -m pip install -q -r backend/requirements.txt || true
  build_frontend || log "  ✘ 回滚构建失败"
  restart_backend || log "  ✘ 回滚重启失败"
  if wait_health; then
    log "✔ 回滚完成，服务已恢复到 ${PREV:0:7} 并可用"
  else
    log "✘ 回滚后服务仍异常，请人工介入（日志：$LOG_FILE）"
  fi
  exit 1
}

# ---------------------------------------------------------------- 主流程
cd "$APP_DIR" 2>/dev/null || { echo "应用目录不存在: $APP_DIR"; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "$APP_DIR 不是 git 仓库"; exit 1; }

PREV="$(git rev-parse HEAD)"
log "===== 部署开始 ====="
log "应用目录：$APP_DIR"
log "当前版本：${PREV:0:7}"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  if [ "$FORCE" = "1" ]; then
    log "⚠ 工作区有未提交改动，--force 下将被丢弃"
  else
    log "✘ 工作区有未提交改动，已中止（确认无误后加 --force）"
    git status --short --untracked-files=no | sed 's/^/    /'
    exit 1
  fi
fi

log "▶ 拉取 origin/$BRANCH"
if ! git fetch origin "$BRANCH" >>"$LOG_FILE" 2>&1; then
  log "  ✘ git fetch 失败（网络或凭证问题）"
  tail -n 10 "$LOG_FILE" | sed 's/^/    /'
  exit 1
fi
TARGET="$(git rev-parse FETCH_HEAD)"
log "  目标版本：${TARGET:0:7}"

if [ "$TARGET" = "$PREV" ] && [ "$FORCE" != "1" ] && [ -f frontend/dist/index.html ]; then
  log "已是最新版本，无需部署（如需强制重建加 --force）"
  wait_health || exit 1
  sync_nginx || NGINX_FAIL=1
  check_nginx || exit 1
  if [ "${NGINX_FAIL:-0}" = "1" ]; then
    log "===== 结束（代码未变更），但 nginx 配置未生效，请查看上方 ✘ ====="
    exit 1
  fi
  log "===== 结束（未变更）====="
  exit 0
fi

log "▶ 切换代码到 ${TARGET:0:7}"
git reset --hard FETCH_HEAD >>"$LOG_FILE" 2>&1 || rollback

run_step "安装后端依赖" "$PY" -m pip install -q -r backend/requirements.txt || rollback
build_frontend || rollback
restart_backend || rollback
wait_health || rollback
sync_nginx || NGINX_FAIL=1
check_nginx || rollback

if [ "$DEEP" = "1" ]; then
  log "▶ 全平台解析巡检"
  if "$PY" ops/healthcheck.py >>"$LOG_FILE" 2>&1; then
    log "  ✔ 巡检通过"
    tail -n 12 "$LOG_FILE" | sed 's/^/    /'
  else
    log "  ⚠ 巡检存在失败平台（部署本身成功，详见 ops/health_status.json）"
    "$PY" ops/healthcheck.py 2>&1 | sed 's/^/    /'
  fi
fi

if [ "${NGINX_FAIL:-0}" = "1" ]; then
  log "===== 部署完成，但 nginx 配置未生效（见上方 ✘，原配置已还原）====="
  exit 1
fi

log "===== 部署成功 ${PREV:0:7} → ${TARGET:0:7} ====="
