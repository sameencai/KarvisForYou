#!/bin/bash
# ============================================================
# Karvis 服务管理脚本
# 用法: ./start.sh [start|stop|restart|status]
# ============================================================

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/karvis.pid"
LOG_DIR="$APP_DIR/logs"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 检测虚拟环境
PYTHON_CMD="python3"
VENV_DIR="$(dirname "$APP_DIR")/venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
fi

get_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return
        fi
        rm -f "$PID_FILE"
    fi
    echo ""
}

do_stop() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 停止服务 (PID: $pid)"
        kill "$pid" 2>/dev/null
        sleep 2
        # 如果还没停，强制 kill
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
        fi
        rm -f "$PID_FILE"
        echo "  ✓ 已停止"
    else
        echo "  服务未运行"
    fi
}

do_start() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        echo "  服务已在运行 (PID: $pid)"
        return 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动 Karvis..."
    cd "$APP_DIR"

    # 启动应用（日志由 logger.py 的 TimedRotatingFileHandler 管理）
    # stderr 仅作为兜底（未被 logger 捕获的异常/crash）
    nohup $PYTHON_CMD app.py >> "$LOG_DIR/stderr.log" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"

    sleep 2
    if kill -0 "$new_pid" 2>/dev/null; then
        echo "  ✓ 启动成功 (PID: $new_pid)"
        echo "  日志目录: $LOG_DIR/"
        echo "  当前日志: $LOG_DIR/karvis.log"
        echo "  历史日志: $LOG_DIR/karvis.log.YYYY-MM-DD"
    else
        echo "  ✗ 启动失败，请检查 $LOG_DIR/stderr.log"
        rm -f "$PID_FILE"
        return 1
    fi
}

do_status() {
    local pid=$(get_pid)
    if [ -n "$pid" ]; then
        echo "Karvis 运行中 (PID: $pid)"
        # 显示日志文件大小
        if [ -f "$LOG_DIR/karvis.log" ]; then
            local size=$(du -h "$LOG_DIR/karvis.log" | cut -f1)
            echo "  当前日志: $LOG_DIR/karvis.log ($size)"
        fi
        # 显示归档日志数量
        local archive_count=$(ls "$LOG_DIR"/karvis.log.* 2>/dev/null | wc -l)
        if [ "$archive_count" -gt 0 ]; then
            echo "  历史归档: $archive_count 个文件"
        fi
    else
        echo "Karvis 未运行"
    fi
}

# ============ 主逻辑 ============
case "${1:-restart}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 1
        do_start
        ;;
    status)
        do_status
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
