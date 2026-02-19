#!/bin/bash
# KarvisForAll 数据定期备份脚本
# 用法: crontab -e → 0 4 * * * /opt/karvis-all/scripts/backup.sh
#
# 功能:
#   1. 将 data/ 目录打包为带日期的 tar.gz
#   2. 保留最近 KEEP_DAYS 天的备份，自动清理旧备份
#   3. 输出日志到 backup.log

set -euo pipefail

# ---- 配置 ----
PROJECT_DIR="${KARVIS_PROJECT_DIR:-/opt/karvis-all}"
DATA_DIR="${PROJECT_DIR}/data"
BACKUP_DIR="${PROJECT_DIR}/backups"
KEEP_DAYS=7
LOG_FILE="${BACKUP_DIR}/backup.log"

# ---- 初始化 ----
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/karvis-data-${TIMESTAMP}.tar.gz"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# ---- 备份 ----
log "开始备份: ${DATA_DIR}"

if [ ! -d "$DATA_DIR" ]; then
    log "错误: 数据目录不存在: ${DATA_DIR}"
    exit 1
fi

# 统计文件数量
FILE_COUNT=$(find "$DATA_DIR" -type f | wc -l | tr -d ' ')
log "数据文件数: ${FILE_COUNT}"

# 打包（排除临时文件）
tar czf "$BACKUP_FILE" -C "$PROJECT_DIR" data/ 2>/dev/null
BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "备份完成: ${BACKUP_FILE} (${BACKUP_SIZE})"

# ---- 清理旧备份 ----
DELETED=0
while IFS= read -r old_file; do
    rm -f "$old_file"
    DELETED=$((DELETED + 1))
done < <(find "$BACKUP_DIR" -name "karvis-data-*.tar.gz" -mtime +${KEEP_DAYS} -type f 2>/dev/null)

if [ "$DELETED" -gt 0 ]; then
    log "清理旧备份: 删除 ${DELETED} 个（保留 ${KEEP_DAYS} 天内）"
fi

# ---- 统计 ----
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "karvis-data-*.tar.gz" -type f | wc -l | tr -d ' ')
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" --exclude="backup.log" 2>/dev/null | cut -f1 || du -sh "$BACKUP_DIR" | cut -f1)
log "当前备份数: ${TOTAL_BACKUPS}, 总占用: ${TOTAL_SIZE}"
log "----"
