#!/usr/bin/env bash
# ============================================================
# [P2] 数据库备份脚本（SQLite / MySQL 双模式，保留最近 N 份）
#
# 用法（建议 crontab 每日执行，RPO=24h）：
#   0 2 * * * /opt/oa-reimbursement/scripts/backup_db.sh >> /var/log/oa-backup.log 2>&1
#
# 环境变量：
#   BACKUP_DIR        备份目录（默认 ./backups）
#   BACKUP_KEEP       保留份数（默认 14）
#   OA_DB_PATH        SQLite 文件路径（默认 ./oa_agent.db）
#   MYSQL_URL         设置后备份 MySQL，格式 user:pass@host:port/db
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"

if [ -n "${MYSQL_URL:-}" ]; then
  # ── MySQL：mysqldump 单事务热备（不锁表）──
  USERPASS="${MYSQL_URL%%@*}"; HOSTDB="${MYSQL_URL##*@}"
  DB_USER="${USERPASS%%:*}"; DB_PASS="${USERPASS#*:}"
  DB_HOST_PORT="${HOSTDB%%/*}"; DB_NAME="${HOSTDB##*/}"
  DB_HOST="${DB_HOST_PORT%%:*}"; DB_PORT="${DB_HOST_PORT##*:}"
  OUT="${BACKUP_DIR}/oa_mysql_${STAMP}.sql.gz"
  mysqldump --single-transaction --routines --triggers \
    -h "${DB_HOST}" -P "${DB_PORT:-3306}" -u "${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" \
    | gzip > "${OUT}"
  echo "[备份] MySQL → ${OUT} ($(du -h "${OUT}" | cut -f1))"
else
  # ── SQLite：.backup 在线热备（WAL 模式安全）──
  DB_FILE="${OA_DB_PATH:-./oa_agent.db}"
  if [ ! -f "${DB_FILE}" ]; then echo "[错误] 找不到数据库文件 ${DB_FILE}"; exit 1; fi
  OUT="${BACKUP_DIR}/oa_sqlite_${STAMP}.db"
  sqlite3 "${DB_FILE}" ".backup '${OUT}'"
  gzip "${OUT}"
  echo "[备份] SQLite → ${OUT}.gz ($(du -h "${OUT}.gz" | cut -f1))"
fi

# ── 滚动清理：仅保留最近 N 份 ──
ls -1t "${BACKUP_DIR}"/oa_*_*.gz 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
echo "[备份] 完成，当前保留 $(ls -1 "${BACKUP_DIR}"/oa_*_*.gz 2>/dev/null | wc -l | tr -d ' ') 份"
