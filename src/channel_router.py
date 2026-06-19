# -*- coding: utf-8 -*-
"""
渠道路由器 — 根据用户所属渠道分发消息。

设计：
- 维护 {channel_name: send_function} 注册表
- send_message(user_id, text) 查用户 channel 自动路由
- 支持运行时注册新渠道
- 兼容老用户（默认 wework）
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    from logger import log
    log(f"[ChannelRouter] {msg}")


# ============ 渠道注册表 ============
_channels = {}   # {"wework": send_fn, "telegram": send_fn}


def register_channel(name, send_fn):
    """注册一个发送渠道"""
    _channels[name] = send_fn
    _log(f"渠道已注册: {name}")


# ============ 用户渠道缓存 ============
_user_channel_cache = {}   # {user_id: "wework" | "telegram"}


def get_user_channel(user_id):
    """获取用户所属渠道（带内存缓存）"""
    if user_id in _user_channel_cache:
        return _user_channel_cache[user_id]

    # Telegram 用户 ID 带 tg_ 前缀
    if user_id.startswith("tg_"):
        _user_channel_cache[user_id] = "telegram"
        return "telegram"

    # 其他情况尝试读 user_config.json
    try:
        from user_context import DATA_DIR
        config_file = os.path.join(DATA_DIR, "users", user_id, "_Karvis", "user_config.json")
        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            channel = config.get("channel", "wework")
            _user_channel_cache[user_id] = channel
            return channel
    except Exception:
        pass

    # 默认企微（兼容老用户）
    _user_channel_cache[user_id] = "wework"
    return "wework"


def set_user_channel(user_id, channel):
    """设置用户渠道（缓存 + 写入 config 由调用方负责）"""
    _user_channel_cache[user_id] = channel


def clear_user_channel_cache(user_id=None):
    """清除渠道缓存"""
    if user_id:
        _user_channel_cache.pop(user_id, None)
    else:
        _user_channel_cache.clear()


# ============ 统一发送 ============

def send_message(user_id, text):
    """统一发送入口 — 根据用户 channel 自动路由"""
    channel = get_user_channel(user_id)
    fn = _channels.get(channel)
    if fn:
        return fn(user_id, text)
    _log(f"未知渠道 {channel} for user {user_id}, 已注册渠道: {list(_channels.keys())}")
    return False


def send_alert(text):
    """管理员告警 — 推送到所有活跃渠道的管理员"""
    results = []
    # 企微管理员
    from config import ADMIN_USER_ID
    if ADMIN_USER_ID and "wework" in _channels:
        try:
            ok = _channels["wework"](ADMIN_USER_ID, text)
            results.append(("wework", ok))
        except Exception as e:
            _log(f"企微告警发送失败: {e}")
            results.append(("wework", False))

    # Telegram 管理员
    try:
        from config import TELEGRAM_ADMIN_CHAT_ID
        if TELEGRAM_ADMIN_CHAT_ID and "telegram" in _channels:
            tg_uid = f"tg_{TELEGRAM_ADMIN_CHAT_ID}"
            ok = _channels["telegram"](tg_uid, text)
            results.append(("telegram", ok))
    except (ImportError, AttributeError):
        pass

    return results
