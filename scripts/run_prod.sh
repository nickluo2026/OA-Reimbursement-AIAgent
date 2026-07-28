#!/usr/bin/env bash
# ============================================================
# [P0] 生产启动脚本：Gunicorn 多进程 + 生产安全基线检查
#
# 用法：
#   FLASK_SECRET_KEY=<64位hex> ./scripts/run_prod.sh
#
# 可选环境变量：
#   BIND                绑定地址（默认 127.0.0.1:8000，由 Nginx 反代对外）
#   GUNICORN_WORKERS    worker 进程数（默认 CPU*2+1）
#   OA_DATABASE_URL     MySQL 连接串（未设置时使用 SQLite + WAL）
#   OA_REDIS_URL        Redis（服务端会话 + 登录限流共享计数）
#   OA_LOG_FORMAT       json（结构化日志，接 ELK/Loki）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# ── 生产环境固定配置 ──
export OA_ENV="${OA_ENV:-production}"
export OA_STRICT_PROD="${OA_STRICT_PROD:-1}"      # 缺失 FLASK_SECRET_KEY 时 fail-fast
export OA_DEMO_SEED="${OA_DEMO_SEED:-0}"          # 生产禁止预置演示数据
export OA_LOG_FORMAT="${OA_LOG_FORMAT:-json}"     # 结构化日志

# ── 基线检查 ──
if [ -z "${FLASK_SECRET_KEY:-}" ]; then
  echo "[错误] 必须设置 FLASK_SECRET_KEY（固定密钥，重启不掉线）"
  echo "       生成: python3 -c 'import secrets; print(secrets.token_hex(32))'"
  exit 1
fi

WORKERS="${GUNICORN_WORKERS:-$(( $(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2) * 2 + 1 ))}"
BIND_ADDR="${BIND:-127.0.0.1:8000}"

if [ -z "${OA_DATABASE_URL:-}" ] && [ "${WORKERS}" -gt 1 ]; then
  echo "[提示] 未配置 OA_DATABASE_URL（MySQL），SQLite 已启用 WAL+busy_timeout，"
  echo "       可支撑小规模并发；正式生产建议按 ADR-005 迁移 MySQL。"
fi
if [ -z "${OA_REDIS_URL:-}" ]; then
  echo "[提示] 未配置 OA_REDIS_URL：会话为 Cookie 模式、登录限流为进程内计数，"
  echo "       多实例部署时请配置 Redis。"
fi

echo "=============================================="
echo "  企业报销智能化系统 — 生产模式 (Gunicorn)"
echo "  bind=${BIND_ADDR}  workers=${WORKERS}"
echo "=============================================="

# 通过 python3 -m 调用，避免 pip --user 安装时 gunicorn 不在 PATH 的问题
exec python3 -m gunicorn "web.app:app" \
  --bind "${BIND_ADDR}" \
  --workers "${WORKERS}" \
  --timeout 120 \
  --graceful-timeout 30 \
  --keep-alive 5 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --access-logfile - \
  --error-logfile - \
  --forwarded-allow-ips "127.0.0.1"
