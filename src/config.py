# -*- coding: utf-8 -*-
"""
KarvisForAll 统一配置
凭证和运行参数集中管理。
路径相关已迁移到 user_context.py（按用户隔离）。
"""
import os

# ============ DeepSeek API (Tier 2/3: Main + Think) ============
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v3.2")

# ============ Claude API (管理员专属 Main + Think) ============
# 使用 OpenAI 兼容格式的代理服务
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_BASE_URL = os.environ.get("CLAUDE_BASE_URL", "https://api.mcxhm.cn")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# ============ Qwen Flash API (Tier 1: Flash) ============
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_BASE_URL = os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")

# ============ Qwen VL (视觉理解) ============
QWEN_VL_MODEL = os.environ.get("QWEN_VL_MODEL", "qwen-vl-max")

# ============ 企业微信（WeWork 应用） ============
CORP_ID = os.environ.get("WEWORK_CORP_ID", "")
AGENT_ID = int(os.environ.get("WEWORK_AGENT_ID", "0"))
CORP_SECRET = os.environ.get("WEWORK_CORP_SECRET", "")
WEWORK_TOKEN = os.environ.get("WEWORK_TOKEN", "")
ENCODING_AES_KEY = os.environ.get("WEWORK_ENCODING_AES_KEY", "")

# ============ 腾讯云 ASR ============
TENCENT_APPID = os.environ.get("TENCENT_APPID", "")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")

# ============ 心知天气 API ============
WEATHER_API_KEY = os.environ.get("SENIVERSE_KEY", "")
WEATHER_CITY = os.environ.get("WEATHER_CITY", "北京")

# ============ 运行参数 ============
MSG_CACHE_EXPIRE_SECONDS = 60
CHECKIN_TIMEOUT_SECONDS = 43200      # 12 小时
RECENT_MESSAGES_LIMIT = 10           # 短期记忆保留条数
PROMPT_CACHE_TTL = 1800              # prompt 文件缓存 30 分钟
STATE_CACHE_TTL = 300                # state 本地缓存 5 分钟

# ============ 主动陪伴参数 ============
COMPANION_SILENT_HOURS = 4
COMPANION_INTERVAL_HOURS = 4
COMPANION_MAX_DAILY = 3
COMPANION_RECENT_HOURS = 2

# ============ Web / 管理员 ============
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
WEB_TOKEN_EXPIRE_HOURS = int(os.environ.get("WEB_TOKEN_EXPIRE_HOURS", "24"))
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "")  # 管理员企微 user_id，用于告警推送

# ============ 告警阈值 ============
ALERT_SLOW_THRESHOLD = int(os.environ.get("ALERT_SLOW_THRESHOLD", "20"))     # 慢请求告警阈值(秒)
ALERT_SLOW_CONSECUTIVE = int(os.environ.get("ALERT_SLOW_CONSECUTIVE", "3"))  # 连续慢请求才告警
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "300"))  # 同类告警冷却(秒)

# ============ V8: 智能调度引擎 ============
SCHEDULER_TICK_MINUTES = 30       # 心跳评估间隔（分钟）
SCHEDULER_DEFAULT_WAKE = "08:00"  # 默认起床时间
SCHEDULER_DEFAULT_SLEEP = "23:30" # 默认入睡时间
SCHEDULER_WEEKEND_SHIFT = 60      # 周末平均晚起分钟数
SCHEDULER_RHYTHM_WINDOW = 7       # 节奏学习滑动窗口（天）
SCHEDULER_PUSH_MAX_DAILY = 6      # 每日所有主动推送总上限
SCHEDULER_MIN_PUSH_GAP = 30       # 两次推送最小间隔（分钟）

# ============ 日志查看 ============
LOG_FILE_KARVISFORALL = os.environ.get("LOG_FILE_KARVISFORALL", "/root/KarvisForAll/logs/app.log")

# ============ 服务端口 ============
SERVER_PORT = int(os.environ.get("SERVER_PORT", "9000"))

# ============ 深度自问（Reflect） ============
REFLECT_COOLDOWN_DAYS = 90       # 同一问题最少间隔天数

# ============ Telegram Bot ============
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

# ============ 渠道控制 ============
# 可选值: "wework", "telegram", "wework,telegram"
ACTIVE_CHANNELS = [c.strip() for c in os.environ.get("ACTIVE_CHANNELS", "wework").split(",") if c.strip()]

# Telegram API 代理地址（国内服务器需要反代）
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
