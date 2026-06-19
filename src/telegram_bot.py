# -*- coding: utf-8 -*-
"""
Telegram Bot 接入层。

职责：
1. 接收 Telegram 消息（Webhook）
2. 解析为 Karvis 统一消息格式
3. 转发到 handle_message()
4. 发送消息到 Telegram
5. 下载 Telegram 媒体文件

使用 requests 直接调用 Telegram Bot API，不引入重型框架。
"""
import sys
import requests
from datetime import datetime, timezone, timedelta

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    from logger import log
    log(f"[Telegram] {msg}")


# ============ Bot API 基础 ============

def _get_bot_api():
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_API_BASE
    return f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}"


def _get_file_api():
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_API_BASE
    return f"{TELEGRAM_API_BASE}/file/bot{TELEGRAM_BOT_TOKEN}"


# ============ 消息发送 ============

def send_telegram_message(user_id, text):
    """
    发送文本消息到 Telegram。

    user_id: "tg_123456789" 格式，自动提取 chat_id
    text: 消息内容
    """
    # 提取 chat_id（去掉 tg_ 前缀）
    chat_id = user_id
    if user_id.startswith("tg_"):
        chat_id = user_id[3:]

    url = f"{_get_bot_api()}/sendMessage"

    # Telegram Markdown 对特殊字符敏感，先尝试 Markdown，失败则用纯文本
    data = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        resp = requests.post(url, json=data, timeout=15)
        result = resp.json()
        ok = result.get("ok", False)
        if not ok:
            _log(f"发送失败: {result.get('description', result)}")
        return ok
    except Exception as e:
        _log(f"发送异常: {e}")
        return False


# ============ 消息解析 ============

def parse_telegram_update(update: dict):
    """
    解析 Telegram Update 为 Karvis 统一消息格式。

    返回: (msg_dict, user_id) 或 (None, None)

    msg_dict 格式与企微解析结果一致:
    {
        "msg_type": "text" | "image" | "voice" | "video",
        "content": "...",
        "media_id": "...",
        "from_user": "tg_123456",
        "msg_id": "...",
    }
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None, None

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return None, None

    user_id = f"tg_{chat_id}"
    msg_id = str(message.get("message_id", ""))

    # 提取发送者信息（用于新用户注册时获取名字）
    from_user = message.get("from", {})
    sender_name = from_user.get("first_name", "")
    if from_user.get("last_name"):
        sender_name += " " + from_user["last_name"]

    # /start 命令 — 当作普通文本处理（触发注册/欢迎）
    if "text" in message and message["text"].strip().startswith("/start"):
        return {
            "msg_type": "text",
            "content": "你好",
            "from_user": user_id,
            "msg_id": msg_id,
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 文本消息
    if "text" in message:
        return {
            "msg_type": "text",
            "content": message["text"],
            "from_user": user_id,
            "msg_id": msg_id,
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 图片（取最大尺寸）
    if "photo" in message:
        photos = message["photo"]
        photo = photos[-1]  # 最大尺寸
        return {
            "msg_type": "image",
            "media_id": photo["file_id"],
            "from_user": user_id,
            "msg_id": msg_id,
            "content": message.get("caption", ""),
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 语音
    if "voice" in message:
        return {
            "msg_type": "voice",
            "media_id": message["voice"]["file_id"],
            "format": "ogg",
            "from_user": user_id,
            "msg_id": msg_id,
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 视频
    if "video" in message:
        return {
            "msg_type": "video",
            "media_id": message["video"]["file_id"],
            "from_user": user_id,
            "msg_id": msg_id,
            "content": message.get("caption", ""),
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 文档
    if "document" in message:
        doc = message["document"]
        return {
            "msg_type": "file",
            "media_id": doc["file_id"],
            "file_name": doc.get("file_name", ""),
            "from_user": user_id,
            "msg_id": msg_id,
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    # 贴纸 — 当作图片处理
    if "sticker" in message:
        sticker = message["sticker"]
        # 动画贴纸跳过
        if sticker.get("is_animated") or sticker.get("is_video"):
            return None, None
        return {
            "msg_type": "image",
            "media_id": sticker.get("file_id", ""),
            "from_user": user_id,
            "msg_id": msg_id,
            "content": sticker.get("emoji", ""),
            "_tg_sender_name": sender_name,
            "_tg_chat_id": chat_id,
        }, user_id

    _log(f"不支持的消息类型: {list(message.keys())}")
    return None, None


# ============ 媒体下载 ============

def download_telegram_media(file_id):
    """
    通过 Telegram Bot API 下载媒体文件。

    返回: (bytes_data, content_type) 或 (None, None)
    """
    try:
        bot_api = _get_bot_api()

        # 1. 获取 file_path
        resp = requests.get(f"{bot_api}/getFile", params={"file_id": file_id}, timeout=10)
        result = resp.json()
        if not result.get("ok"):
            _log(f"getFile 失败: {result}")
            return None, None
        file_path = result["result"]["file_path"]

        # 2. 下载文件
        download_url = f"{_get_file_api()}/{file_path}"
        resp = requests.get(download_url, timeout=30)
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            _log(f"媒体下载成功: {len(resp.content)} bytes, type={ct}")
            return resp.content, ct
        else:
            _log(f"媒体下载失败: HTTP {resp.status_code}")
            return None, None
    except Exception as e:
        _log(f"媒体下载异常: {e}")
        return None, None


# ============ Webhook 路由注册 ============

def register_telegram_routes(app):
    """注册 Telegram Webhook 端点到 Flask app"""
    from flask import request as flask_request, jsonify
    import threading

    @app.route("/telegram", methods=["POST"])
    def telegram_webhook():
        """
        接收 Telegram Update。

        流程与 /wework 对称:
        1. 验证 secret_token（可选）
        2. 解析 Update JSON
        3. 去重
        4. 异步处理
        """
        # 可选：验证 Webhook secret
        try:
            from config import TELEGRAM_WEBHOOK_SECRET
            if TELEGRAM_WEBHOOK_SECRET:
                header_secret = flask_request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
                if header_secret != TELEGRAM_WEBHOOK_SECRET:
                    _log(f"Webhook secret 验证失败")
                    return jsonify({"ok": False}), 403
        except (ImportError, AttributeError):
            pass

        update = flask_request.get_json(silent=True)
        if not update:
            return jsonify({"ok": True})

        msg, user_id = parse_telegram_update(update)
        if not msg or not user_id:
            return jsonify({"ok": True})

        # 去重
        from app import is_duplicate_msg
        msg_key = f"tg_{msg.get('msg_id', '')}"
        if is_duplicate_msg(msg_key):
            return jsonify({"ok": True})

        # 异步处理（快速响应 Telegram，必须 <10s）
        def _process():
            try:
                from app import handle_message
                handle_message(msg, user_id)
            except Exception as e:
                _log(f"处理消息异常: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)

        threading.Thread(target=_process, daemon=True).start()

        return jsonify({"ok": True})

    _log("Webhook 路由已注册: POST /telegram")


# ============ Webhook 管理 ============

def setup_telegram_webhook(base_url):
    """向 Telegram 注册 Webhook URL（服务启动时调用）"""
    webhook_url = f"{base_url}/telegram"
    url = f"{_get_bot_api()}/setWebhook"

    data = {
        "url": webhook_url,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }

    # 添加 secret_token（如配置了）
    try:
        from config import TELEGRAM_WEBHOOK_SECRET
        if TELEGRAM_WEBHOOK_SECRET:
            data["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    except (ImportError, AttributeError):
        pass

    try:
        resp = requests.post(url, json=data, timeout=10)
        result = resp.json()
        ok = result.get("ok", False)
        _log(f"setWebhook {'成功' if ok else '失败'}: {result.get('description', '')}")
        return ok
    except Exception as e:
        _log(f"setWebhook 异常: {e}")
        return False


def get_webhook_info():
    """查询当前 Webhook 状态"""
    try:
        resp = requests.get(f"{_get_bot_api()}/getWebhookInfo", timeout=10)
        return resp.json().get("result", {})
    except Exception:
        return {}
