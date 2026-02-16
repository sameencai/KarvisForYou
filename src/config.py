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
