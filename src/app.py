# -*- coding: utf-8 -*-
"""
Karvis 消息网关
职责：接收企微消息 → 下载媒体/ASR → 构造 payload → 交给 brain.process()
不做任何业务判断，所有逻辑由大脑决定。
"""
# 加载 .env 文件（Lite 模式 / 本地开发）
import os
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass  # 未安装 python-dotenv 时跳过

from flask import Flask, request
import json
import time
import sys
import hashlib
import base64
import requests
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

from config import (
    CORP_ID, CORP_SECRET, AGENT_ID,
    WEWORK_TOKEN, ENCODING_AES_KEY,
    TENCENT_APPID, TENCENT_SECRET_ID, TENCENT_SECRET_KEY,
    MSG_CACHE_EXPIRE_SECONDS,
    WEATHER_API_KEY, WEATHER_CITY,
    SCHEDULER_TICK_MINUTES, SCHEDULER_DEFAULT_WAKE, SCHEDULER_DEFAULT_SLEEP,
    SCHEDULER_WEEKEND_SHIFT, SCHEDULER_PUSH_MAX_DAILY, SCHEDULER_MIN_PUSH_GAP,
    SERVER_PORT,
)
from user_context import (
    get_or_create_user, get_all_active_users,
    increment_message_count, is_user_suspended,
    DATA_DIR, SYSTEM_DIR, DAILY_MESSAGE_LIMIT
)

# 异步处理端点的公网 URL（SCF 部署后填入，用于企微 5 秒超时的异步转发）
PROCESS_ENDPOINT_URL = os.environ.get("PROCESS_ENDPOINT_URL", "http://127.0.0.1:9000/process")
from wework_crypto import WXBizMsgCrypt
from storage import IO as OneDriveIO  # 统一存储接口（OneDrive 或 Lite 本地模式）
import brain

app = Flask(__name__)

# 过滤健康检查日志，避免 Nginx/Docker 的 GET / 刷屏
import logging
class _HealthCheckFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return '"GET / ' not in msg and '"GET /health' not in msg
logging.getLogger('werkzeug').addFilter(_HealthCheckFilter())

# 注册 Web 路由 Blueprint
from web_routes import web_bp, api_bp
app.register_blueprint(web_bp, url_prefix="/web")
app.register_blueprint(api_bp, url_prefix="/api")


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============ 企微 access_token 缓存 ============
_wework_token_cache = {"token": None, "expire_time": 0}


def get_wework_access_token():
    now = time.time()
    if _wework_token_cache["token"] and _wework_token_cache["expire_time"] > now:
        return _wework_token_cache["token"]
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={CORP_SECRET}"
    resp = requests.get(url, timeout=10)
    result = resp.json()
    if result.get("errcode") == 0:
        _wework_token_cache["token"] = result["access_token"]
        _wework_token_cache["expire_time"] = now + result["expires_in"] - 200
        return result["access_token"]
    _log(f"[企微] token 获取失败: {result}")
    return None


# ============ 消息发送 ============

def send_wework_message(user_id, content):
    """发送企业微信文本消息"""
    token = get_wework_access_token()
    if not token:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    data = {
        "touser": user_id,
        "msgtype": "text",
        "agentid": AGENT_ID,
        "text": {"content": content}
    }
    resp = requests.post(url, json=data, timeout=10)
    result = resp.json()
    ok = result.get("errcode") == 0
    if not ok:
        _log(f"[回复] 发送失败: {result}")
    return ok


# ============ 消息去重 ============
_processed_msg_cache = {}


def is_duplicate_msg(msg_id):
    if not msg_id:
        return False
    now = time.time()
    # 清理过期
    expired = [k for k, v in _processed_msg_cache.items() if v < now]
    for k in expired:
        del _processed_msg_cache[k]
    if msg_id in _processed_msg_cache:
        _log(f"[去重] 跳过: {msg_id}")
        return True
    _processed_msg_cache[msg_id] = now + MSG_CACHE_EXPIRE_SECONDS
    return False


# ============ 媒体下载 ============

def download_wework_media(media_id):
    """从企微下载临时素材，返回 (bytes, content_type) 或 (None, None)"""
    token = get_wework_access_token()
    if not token:
        return None, None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    resp = requests.get(url, timeout=30)
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type or "text/plain" in content_type:
        _log(f"[素材] 下载失败: {resp.text[:200]}")
        return None, None
    _log(f"[素材] 下载成功: size={len(resp.content)}, type={content_type}")
    return resp.content, content_type


# ============ 附件上传 ============

BEIJING_TZ = timezone(timedelta(hours=8))


def generate_attachment_name(msg_type, ext):
    ts = datetime.now(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{msg_type}.{ext}"


def upload_attachment(data, msg_type, ext, ctx, content_type="application/octet-stream"):
    """上传附件到用户 attachments 目录，返回完整路径或 None"""
    filename = generate_attachment_name(msg_type, ext)
    file_path = f"{ctx.attachments_path}/{filename}"
    ok = OneDriveIO.upload_binary(file_path, data, content_type)
    return file_path if ok else None


# ============ ASR 语音识别 ============

def recognize_voice(audio_data, voice_format="amr"):
    """腾讯云录音文件识别极速版，降级到一句话识别"""
    import hmac

    if not TENCENT_APPID:
        _log("[ASR] 未配置 APPID，降级到一句话识别")
        return _recognize_voice_sentence(audio_data)

    try:
        timestamp = int(time.time())
        params = {
            "convert_num_mode": 1,
            "engine_type": "16k_zh",
            "filter_dirty": 0,
            "filter_modal": 0,
            "filter_punc": 0,
            "first_channel_only": 1,
            "secretid": TENCENT_SECRET_ID,
            "speaker_diarization": 0,
            "timestamp": timestamp,
            "voice_format": voice_format,
            "word_info": 0,
        }
        query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = f"POSTasr.cloud.tencent.com/asr/flash/v1/{TENCENT_APPID}?{query_str}"
        signature = base64.b64encode(
            hmac.new(TENCENT_SECRET_KEY.encode('utf-8'),
                     sign_str.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')

        url = f"https://asr.cloud.tencent.com/asr/flash/v1/{TENCENT_APPID}?{query_str}"
        headers = {
            "Host": "asr.cloud.tencent.com",
            "Authorization": signature,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(audio_data)),
        }
        resp = requests.post(url, headers=headers, data=audio_data, timeout=30)
        result = resp.json()
        _log(f"[ASR极速版] code={result.get('code')}")

        if result.get("code") != 0:
            _log(f"[ASR极速版] 失败: {result.get('message')}")
            return _recognize_voice_sentence(audio_data)

        flash_result = result.get("flash_result", [])
        if flash_result:
            text = flash_result[0].get("text", "")
            _log(f"[ASR极速版] 识别: {text[:80]}")
            return text if text else None
        return None
    except Exception as e:
        _log(f"[ASR极速版] 异常: {e}")
        return _recognize_voice_sentence(audio_data)


def _recognize_voice_sentence(audio_data):
    """降级：腾讯云一句话识别"""
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.asr.v20190614 import asr_client, models

        cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "asr.tencentcloudapi.com"
        httpProfile.reqTimeout = 30
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile

        client = asr_client.AsrClient(cred, "", clientProfile)
        req = models.SentenceRecognitionRequest()
        req.EngSerViceType = "16k_zh"
        req.SourceType = 1
        req.VoiceFormat = "amr"
        req.Data = base64.b64encode(audio_data).decode('utf-8')
        req.DataLen = len(audio_data)

        resp = client.SentenceRecognition(req)
        _log(f"[ASR一句话] 成功: {resp.Result[:50] if resp.Result else 'empty'}")
        return resp.Result
    except Exception as e:
        _log(f"[ASR一句话] 失败: {e}")
        return None


# ============ XML 消息解析 ============

def parse_wechat_message(xml_data):
    """解析企微 XML 消息"""
    root = ET.fromstring(xml_data)
    msg_type = root.find('MsgType').text
    from_user = root.find('FromUserName').text
    result = {'msg_type': msg_type, 'from_user': from_user}

    msg_id = root.find('MsgId')
    if msg_id is not None:
        result['msg_id'] = msg_id.text

    if msg_type == 'text':
        result['content'] = root.find('Content').text
    elif msg_type == 'image':
        media_id = root.find('MediaId')
        if media_id is not None:
            result['media_id'] = media_id.text
    elif msg_type == 'voice':
        media_id = root.find('MediaId')
        fmt = root.find('Format')
        if media_id is not None:
            result['media_id'] = media_id.text
        if fmt is not None:
            result['format'] = fmt.text
    elif msg_type == 'video':
        media_id = root.find('MediaId')
        if media_id is not None:
            result['media_id'] = media_id.text
    elif msg_type == 'link':
        for tag in ('Title', 'Description', 'Url'):
            node = root.find(tag)
            if node is not None:
                result[tag.lower()] = node.text

    return result


# ============ F1: 链接内容抓取 ============

import re

_URL_PATTERN = re.compile(
    r'https?://[^\s<>"\')\]]+',
    re.IGNORECASE
)


def _extract_url(text):
    """
    从文本中提取 URL。
    仅当文本主体是 URL 时才提取（纯 URL 或 URL + 少量描述文字）。
    避免对正常聊天中偶尔出现的 URL 做不必要的抓取。
    """
    text = text.strip()
    match = _URL_PATTERN.search(text)
    if not match:
        return None
    url = match.group(0)
    # 只有当 URL 占文本大部分时才抓取（纯 URL 或 URL + 简短描述）
    non_url_text = text.replace(url, "").strip()
    if len(non_url_text) <= 30:
        return url
    return None

def _fetch_link_content(url):
    """
    F1: 抓取链接正文内容，失败返回空字符串（优雅降级）。
    支持微信公众号文章、普通网页。截断到 2000 字符。
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=5,
                           allow_redirects=True, verify=True)
        resp.encoding = resp.apparent_encoding or 'utf-8'

        if resp.status_code != 200:
            _log(f"[链接抓取] HTTP {resp.status_code}: {url[:80]}")
            return ""

        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type and 'text/plain' not in content_type:
            _log(f"[链接抓取] 非网页内容({content_type}): {url[:80]}")
            return ""

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 移除无用标签
        for tag in soup.find_all(['script', 'style', 'nav', 'header',
                                   'footer', 'aside', 'iframe']):
            tag.decompose()

        # 优先取 article 标签（通用）或微信文章专用结构
        article = (soup.find('article')
                   or soup.find('div', class_='rich_media_content')
                   or soup.find('body'))

        if not article:
            _log(f"[链接抓取] 无法提取正文: {url[:80]}")
            return ""

        text = article.get_text(separator='\n', strip=True)
        result = text[:2000] if text else ""
        _log(f"[链接抓取] 成功: {len(result)} 字符, url={url[:80]}")
        return result
    except Exception as e:
        _log(f"[链接抓取] 异常({e}): {url[:80]}")
        return ""


# ============ 消息 → Payload 转换（网关核心） ============

def build_payload(msg, ctx):
    """
    将企微原始消息转换为 Karvis payload。
    处理媒体下载、附件上传、ASR，但不做任何业务判断。
    返回 payload dict。
    """
    msg_type = msg['msg_type']
    user_id = msg.get('from_user', '')
    payload = {"user_id": user_id}

    if msg_type == 'text':
        content = msg.get('content', '')
        if content.startswith('/help') or content.startswith('帮助'):
            # 帮助命令直接在网关层处理
            return None, 'Karvis 🤖\n\n发送任何内容，我会帮你记录到 Obsidian。\n支持：文字、图片、语音、视频、链接\n\n打卡相关：说"打卡"开始每日复盘'
        payload["type"] = "text"
        payload["text"] = content
        # F1: 检测纯 URL 文本，自动抓取网页正文
        url = _extract_url(content)
        if url:
            page_content = _fetch_link_content(url)
            if page_content:
                payload["page_content"] = page_content
                payload["detected_url"] = url
        return payload, None

    elif msg_type == 'image':
        media_id = msg.get('media_id', '')
        if not media_id:
            return None, "无法获取图片"
        data, content_type = download_wework_media(media_id)
        if not data:
            return None, "图片下载失败"
        ext = "jpg"
        if "png" in (content_type or ""):
            ext = "png"
        elif "gif" in (content_type or ""):
            ext = "gif"
        attachment = upload_attachment(data, "img", ext, ctx, content_type or "image/jpeg")
        if not attachment:
            return None, "图片上传失败"
        payload["type"] = "image"
        payload["attachment"] = attachment
        # 将图片 base64 传给 brain，用于千问 VL 图像理解
        payload["image_base64"] = base64.b64encode(data).decode("utf-8")
        return payload, None

    elif msg_type == 'voice':
        media_id = msg.get('media_id', '')
        audio_format = msg.get('format', 'amr')
        if not media_id:
            return None, "无法获取语音"
        data, content_type = download_wework_media(media_id)
        if not data:
            return None, "语音下载失败"
        ext = audio_format.lower() if audio_format else "amr"
        attachment = upload_attachment(data, "voice", ext, ctx, content_type or "audio/amr")
        recognized_text = recognize_voice(data, voice_format=ext) or ""
        payload["type"] = "voice"
        payload["text"] = recognized_text
        payload["attachment"] = attachment or ""
        return payload, None

    elif msg_type == 'video':
        media_id = msg.get('media_id', '')
        if not media_id:
            return None, "无法获取视频"
        data, content_type = download_wework_media(media_id)
        if not data:
            return None, "视频下载失败"
        size_mb = len(data) / (1024 * 1024)
        _log(f"[视频] 大小={size_mb:.1f}MB")
        attachment = upload_attachment(data, "video", "mp4", ctx, content_type or "video/mp4")
        if not attachment:
            return None, "视频上传失败"
        payload["type"] = "video"
        payload["attachment"] = attachment
        return payload, None

    elif msg_type == 'link':
        payload["type"] = "link"
        payload["title"] = msg.get('title', '链接')
        payload["url"] = msg.get('url', '')
        payload["description"] = msg.get('description', '')[:200]
        # F1: 抓取网页正文内容
        if payload["url"]:
            payload["content"] = _fetch_link_content(payload["url"])
        return payload, None

    else:
        return None, f"暂不支持该消息类型: {msg_type}"


# ============ 消息处理主流程 ============

def handle_message(msg, user_id):
    """
    网关主处理流程：
    1. 获取 UserContext（自动注册新用户）
    2. 消息限额检查
    3. 构造 payload（含媒体处理）
    4. 交给 brain.process()
    5. 发送回复
    """
    t0 = time.time()
    msg_type = msg.get('msg_type', '')
    _log(f"[handle_message] === 开始处理 user={user_id}, msg_type={msg_type} ===")

    # event 类型（关注、进入应用等）静默忽略，不回复不计数
    if msg_type == 'event':
        _log(f"[handle_message] 忽略 event 类型消息, user={user_id}")
        return

    try:
        # 检查用户是否被挂起
        if is_user_suspended(user_id):
            _log(f"[handle_message] 用户 {user_id} 已被挂起，拒绝处理")
            send_wework_message(user_id, "你的账号已被暂停使用，如有疑问请联系管理员。")
            return

        # 获取/创建用户上下文
        ctx, is_new = get_or_create_user(user_id)
        _log(f"[handle_message] 用户上下文已获取: is_new={is_new}, base_dir={ctx.base_dir}")

        # 新用户欢迎消息
        if is_new:
            _log(f"[handle_message] 新用户 {user_id}，发送欢迎消息")
            welcome = (
                "嗨～我是 Karvis 🤖\n"
                "你的 AI 生活助手，住在企业微信里。\n\n"
                "先认识一下吧，你希望我怎么称呼你？\n"
                "（直接说「叫我XX」就好~）"
            )
            send_wework_message(user_id, welcome)
            return

        # ============ 新用户引导流程（onboarding） ============
        config = ctx.get_user_config()
        onboarding = config.get("onboarding_step", 0)

        if onboarding > 0 and msg_type == 'text':
            content = msg.get('content', '').strip()
            _log(f"[onboarding] step={onboarding}, user={user_id}, content={content[:50]}")

            # 任何阶段说"跳过"都结束引导
            if content in ('跳过', '算了', 'skip'):
                config["onboarding_step"] = 0
                ctx.save_user_config(config)
                send_wework_message(user_id, "没问题！有什么想法随时发给我就好～")
                return

            if onboarding == 1:
                # 等昵称 — 用模型提取，能处理各种自然表达
                from brain import call_llm
                extract_prompt = (
                    "用户在设置昵称，请从下面这句话中提取出用户希望被称呼的昵称。\n"
                    "只返回昵称本身，不要任何解释、引号或标点。\n"
                    "如果无法识别，返回空。\n\n"
                    f"用户说：{content}"
                )
                nickname = call_llm(
                    [{"role": "user", "content": extract_prompt}],
                    model_tier="flash", max_tokens=20, temperature=0
                )
                nickname = (nickname or "").strip().strip('"\'""''')
                if not nickname:
                    send_wework_message(user_id, "没听清名字呢，再说一次？直接打名字就行~")
                    return

                # 保存昵称
                config["nickname"] = nickname
                config["onboarding_step"] = 2
                ctx.save_user_config(config)

                from user_context import update_user_nickname
                update_user_nickname(user_id, nickname)

                reply = (
                    f"好的{nickname}！以后就这么叫你啦～\n\n"
                    f"来试试我的核心功能吧 👇\n"
                    f"随便发句话给我，比如：\n"
                    f"「今天天气真好，心情不错」"
                )
                send_wework_message(user_id, reply)
                return

            elif onboarding == 2:
                # 等第一条笔记 — 正常处理但加引导提示
                config["onboarding_step"] = 3
                ctx.save_user_config(config)
                # 不 return，继续走正常 brain 流程

            elif onboarding == 3:
                # 第三步或之后的消息 — 结束引导，正常处理
                config["onboarding_step"] = 0
                ctx.save_user_config(config)
                # 不 return，继续正常流程

        elif onboarding > 0 and msg_type != 'text':
            # 非文本消息（图片/语音等）在引导阶段也推进
            if onboarding == 1:
                send_wework_message(user_id, "先告诉我你的名字吧～直接打名字就行~")
                return
            else:
                # step 2/3 收到非文本，也正常处理并推进
                config["onboarding_step"] = 0
                ctx.save_user_config(config)

        # 引导阶段也正常计数
        # 消息计数 + 限额检查
        count, over_limit = increment_message_count(user_id)
        _log(f"[handle_message] 消息计数: count={count}, limit={DAILY_MESSAGE_LIMIT}, over={over_limit}")
        if over_limit:
            send_wework_message(user_id, f"今日消息已达上限（{DAILY_MESSAGE_LIMIT} 条），明天再来吧~")
            return

        payload, quick_reply = build_payload(msg, ctx)
        _log(f"[handle_message] payload构建完成: type={payload.get('type') if payload else 'None'}, "
             f"quick_reply={'有' if quick_reply else '无'}")

        # 帮助命令或媒体处理失败
        if payload is None:
            if quick_reply and user_id:
                send_wework_message(user_id, quick_reply)
            return

        # 交给大脑（传入发送回调，实现先回复后保存）
        def _send_reply(text):
            if user_id:
                send_wework_message(user_id, text)

        _log(f"[handle_message] 交给 brain.process(), payload_type={payload.get('type')}")
        result = brain.process(payload, send_fn=_send_reply, ctx=ctx)
        reply = result.get("reply") if result else None
        already_sent = result.get("already_sent", False) if result else False
        _log(f"[handle_message] brain 返回: reply={'有' if reply else '无'}({len(reply) if reply else 0}字), "
             f"already_sent={already_sent}")

        # 如果 brain 已经通过 send_fn 发送，不再重复发送
        if reply and user_id and not already_sent:
            send_wework_message(user_id, reply)

        # 引导阶段追加提示
        config_now = ctx.get_user_config()
        ob_step = config_now.get("onboarding_step", 0)
        nickname = config_now.get("nickname") or ""

        if ob_step == 3:
            # 刚完成第一条笔记（step 2→3），追加待办引导
            time.sleep(0.5)
            guide = (
                f"✨ 看，你的第一条记录已经保存好了！\n\n"
                f"再试试待办功能？直接说：\n"
                f"「帮我添加待办 明天买咖啡」"
            )
            send_wework_message(user_id, guide)

        elif ob_step == 0 and onboarding == 3:
            # 刚完成引导（step 3→0）— 生成 Web 链接一并发出
            time.sleep(0.5)

            # 自动生成查看链接
            from user_context import generate_token
            token = generate_token(user_id)
            import os as _os
            domain = _os.environ.get("WEB_DOMAIN", "127.0.0.1:9000")
            # IP 地址用 http，有域名才用 https
            _is_ip = all(part.isdigit() for part in domain.split(":")[0].split("."))
            scheme = "http" if _is_ip or "127.0.0.1" in domain or "localhost" in domain else "https"
            web_url = f"{scheme}://{domain}/web/login?token={token}"

            final = (
                f"🎉 太棒了{nickname}！你已经掌握了核心用法：\n\n"
                f"💬 发消息 → 自动记笔记\n"
                f"✅ 说「添加待办」→ 管理任务\n"
                f"📊 每晚自动生成日报\n"
                f"🌙 晚上 9 点会邀请你打卡复盘\n\n"
                f"📱 你还可以在浏览器里查看所有数据：\n"
                f"{web_url}\n\n"
                f"链接 24 小时有效，过期了跟我说「给我查看链接」就行～\n\n"
                f"还有更多玩法慢慢发现，有什么想法随时告诉我！"
            )
            send_wework_message(user_id, final)

        _log(f"[handle_message] === 处理完成 user={user_id}, 耗时={time.time()-t0:.1f}s ===")

    except Exception as e:
        _log(f"[handle_message] === 处理异常 user={user_id}, 耗时={time.time()-t0:.1f}s ===")
        _log(f"[handle_message] 异常: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        if user_id:
            send_wework_message(user_id, "处理消息时出错了，请稍后重试")


# ============ 加解密器 ============
wx_crypt = WXBizMsgCrypt(WEWORK_TOKEN, ENCODING_AES_KEY, CORP_ID)


# ============ Flask 路由 ============

@app.route('/wework', methods=['GET', 'POST'])
def wework():
    """企业微信入口"""
    if request.method == 'GET':
        msg_signature = request.args.get('msg_signature', '')
        timestamp = request.args.get('timestamp', '')
        nonce = request.args.get('nonce', '')
        echostr = request.args.get('echostr', '')
        reply = wx_crypt.verify_url(msg_signature, timestamp, nonce, echostr)
        return reply if reply else "verify failed"

    if request.method == 'POST':
        try:
            xml_data = request.data.decode('utf-8')
            _log("[企微] 收到 POST")

            msg_signature = request.args.get('msg_signature', '')
            timestamp = request.args.get('timestamp', '')
            nonce = request.args.get('nonce', '')

            # 解密
            root = ET.fromstring(xml_data)
            encrypt_node = root.find('Encrypt')
            if encrypt_node is not None:
                decrypted_xml = wx_crypt.decrypt_msg(
                    msg_signature, timestamp, nonce, encrypt_node.text)
                if not decrypted_xml:
                    _log("[企微] 解密失败")
                    return "success"
                msg = parse_wechat_message(decrypted_xml)
            else:
                msg = parse_wechat_message(xml_data)

            user_id = msg.get('from_user', '')
            msg_id = msg.get('msg_id', '')
            _log(f"[企微] user={user_id}, type={msg['msg_type']}, id={msg_id}")

            # 消息去重
            if msg_id and is_duplicate_msg(msg_id):
                return "success"

            # 异步处理：通过公网 URL 调用自己的 /process 端点
            # 这会触发一个全新的 SCF 请求，不受企微 5 秒超时影响
            payload_data = json.dumps({
                "msg": msg,
                "user_id": user_id
            }, ensure_ascii=False)

            def fire_and_forget():
                try:
                    resp = requests.post(
                        PROCESS_ENDPOINT_URL,
                        data=payload_data.encode('utf-8'),
                        headers={"Content-Type": "application/json"},
                        timeout=300  # 等完整响应，日报等重任务可能需要更久
                    )
                    _log(f"[触发] /process 返回: {resp.status_code}")
                except Exception as e:
                    _log(f"[触发] /process 调用异常: {e}")

            t = threading.Thread(target=fire_and_forget)
            t.start()

            # 等一小段时间确保请求已发出（TCP 握手完成）
            time.sleep(0.3)

            _log(f"[企微] 已触发 /process，立即返回 success")
            return "success"

        except Exception as e:
            _log(f"[企微] 错误: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            return "success"

    return "success"


@app.route('/process', methods=['POST'])
def process_endpoint():
    """内部异步处理端点：接收消息并调用 brain 处理"""
    try:
        data = request.get_json(force=True)
        msg = data.get("msg", {})
        user_id = data.get("user_id", "")
        _log(f"[/process] 开始处理 type={msg.get('msg_type')}, user={user_id}")
        handle_message(msg, user_id)
        _log(f"[/process] 处理完成")
        return "ok"
    except Exception as e:
        _log(f"[/process] 异常: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return "error"


@app.route('/system', methods=['POST'])
def system_endpoint():
    """系统端点：定时器/手动触发的 system action（支持多用户遍历）"""
    try:
        data = request.get_json(force=True)
        action = data.get("action", "")
        target_user = data.get("user_id", "")
        _log(f"[/system] action={action}, user={target_user or 'all'}")

        if action == "refresh_cache":
            from memory import invalidate_all_caches
            from user_context import cleanup_expired_tokens
            invalidate_all_caches()
            removed = cleanup_expired_tokens()
            _log(f"[/system] 缓存已全部清除, 清理过期令牌 {removed} 个")
            return json.dumps({"ok": True, "action": "refresh_cache", "tokens_cleaned": removed})

        # V8: 智能调度引擎（daily_init / scheduler_tick 遍历所有用户）
        if action in ("daily_init", "scheduler_tick"):
            user_ids = [target_user] if target_user else get_all_active_users()
            results = []
            for uid in user_ids:
                try:
                    ctx, _ = get_or_create_user(uid)
                    if action == "daily_init":
                        r = _daily_init(uid, ctx)
                    else:
                        r = _scheduler_tick(uid, ctx)
                    results.append({"user_id": uid, **r})
                except Exception as e:
                    _log(f"[/system] V8 {action} 用户 {uid} 失败: {e}")
                    results.append({"user_id": uid, "ok": False, "error": str(e)})
            return json.dumps({"ok": True, "action": action, "results": results}, ensure_ascii=False)

        # 如果指定了 user_id，只处理该用户；否则遍历所有活跃用户
        if target_user:
            user_ids = [target_user]
        else:
            user_ids = get_all_active_users()
            _log(f"[/system] 遍历 {len(user_ids)} 个活跃用户")

        total_results = []

        for uid in user_ids:
            try:
                ctx, _ = get_or_create_user(uid)
                result = _run_system_action_for_user(action, data, uid, ctx)
                total_results.append({"user_id": uid, **result})
            except Exception as e:
                _log(f"[/system] 用户 {uid} 执行 {action} 失败: {e}")
                total_results.append({"user_id": uid, "ok": False, "error": str(e)})

            # 多用户遍历时随机延迟，避免 API 限流
            if len(user_ids) > 1:
                import random
                time.sleep(random.uniform(1, 3))

        _log(f"[/system] {action} 完成, 共处理 {len(total_results)} 个用户")
        return json.dumps({"ok": True, "action": action, "results": total_results},
                          ensure_ascii=False)

    except Exception as e:
        _log(f"[/system] 异常: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return json.dumps({"ok": False, "error": str(e)})


def _run_system_action_for_user(action, data, uid, ctx):
    """为单个用户执行系统动作，返回结果 dict"""
    from memory import read_state_cached, write_state_and_update_cache
    _log(f"[system_action] 开始执行: action={action}, user={uid}")
    t0 = time.time()

    if action == "todo_remind":
        from skills.todo_manage import check_reminders
        state = read_state_cached(ctx) or {}
        result = check_reminders(state)
        messages = result.get("messages", [])
        state_updates = result.get("state_updates", {})
        _log(f"[system_action] todo_remind: {len(messages)} 条提醒, {len(state_updates)} 个状态更新")
        for msg in messages:
            send_wework_message(uid, msg)
        if state_updates:
            for k, v in state_updates.items():
                state[k] = v
            write_state_and_update_cache(state, ctx)
        _log(f"[system_action] todo_remind 完成, user={uid}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "sent": len(messages)}

    if action in ("morning_report", "evening_checkin", "daily_report"):
        context = {}
        try:
            todo_content = OneDriveIO.read_text(ctx.todo_file)
            if todo_content:
                context["todo"] = todo_content[:2000]
            quick_notes = OneDriveIO.read_text(ctx.quick_notes_file)
            if quick_notes:
                context["quick_notes"] = quick_notes[:1000]
        except Exception as e:
            _log(f"[/system] [{uid}] 读取上下文失败（不影响主流程）: {e}")

        if action == "morning_report":
            try:
                context["time_capsule"] = _build_time_capsule(ctx)
            except Exception as e:
                _log(f"[/system] [{uid}] 时间胶囊读取失败: {e}")

            try:
                weather = _build_weather_context()
                if weather:
                    context["weather"] = weather
            except Exception as e:
                _log(f"[/system] [{uid}] 天气获取失败: {e}")

            try:
                from skills.decision_track import get_due_decisions
                _state = read_state_cached(ctx) or {}
                due_decisions = get_due_decisions(_state)
                if due_decisions:
                    context["due_decisions"] = due_decisions
            except Exception as e:
                _log(f"[/system] [{uid}] 到期决策读取失败: {e}")

            try:
                from skills.habit_coach import check_experiment_expiry, get_experiment_summary_for_review
                _state = read_state_cached(ctx) or {}
                expiry_msg = check_experiment_expiry(_state)
                if expiry_msg:
                    context["experiment_expired"] = expiry_msg
                exp_summary = get_experiment_summary_for_review(_state)
                if exp_summary:
                    context["active_experiment"] = exp_summary
            except Exception as e:
                _log(f"[/system] [{uid}] 实验上下文读取失败: {e}")

        if action in ("morning_report", "evening_checkin"):
            try:
                context["nudge"] = _build_nudge_context(ctx)
            except Exception as e:
                _log(f"[/system] [{uid}] nudge 上下文读取失败: {e}")

        if action == "evening_checkin":
            try:
                _state = read_state_cached(ctx) or {}
                daily_top3 = _state.get("daily_top3", {})
                today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
                if daily_top3 and daily_top3.get("date") == today_str:
                    context["daily_top3"] = daily_top3
            except Exception as e:
                _log(f"[/system] [{uid}] daily_top3 读取失败: {e}")

        payload = {
            "type": "system",
            "action": action,
            "user_id": uid,
            "context": context
        }
        _log(f"[system_action] {action}: 调用 brain.process(), context_keys={list(context.keys())}")
        result = brain.process(payload, ctx=ctx)
        reply = result.get("reply") if result else None
        if reply:
            _log(f"[system_action] {action}: 发送回复给 {uid}, len={len(reply)}")
            send_wework_message(uid, reply)
        _log(f"[system_action] {action} 完成, user={uid}, has_reply={bool(reply)}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "has_reply": bool(reply)}

    if action == "mood_generate":
        from skills.mood_diary import execute as mood_execute
        state = read_state_cached(ctx) or {}
        _log(f"[system_action] mood_generate: 开始生成情绪日记, user={uid}")
        result = mood_execute(data, state, ctx)
        write_state_and_update_cache(state, ctx)
        reply = result.get("reply") if result else None
        if reply:
            send_wework_message(uid, reply)
        _log(f"[system_action] mood_generate 完成, user={uid}, has_reply={bool(reply)}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "has_reply": bool(reply)}

    if action == "weekly_review":
        from skills.weekly_review import execute as weekly_execute
        state = read_state_cached(ctx) or {}
        _log(f"[system_action] weekly_review: 开始生成周回顾, user={uid}")
        result = weekly_execute(data, state, ctx)
        write_state_and_update_cache(state, ctx)
        reply = result.get("reply") if result else None
        if reply:
            send_wework_message(uid, reply)
        _log(f"[system_action] weekly_review 完成, user={uid}, has_reply={bool(reply)}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "has_reply": bool(reply)}

    if action == "nudge_check":
        messages = _run_nudge_check(ctx)
        _log(f"[system_action] nudge_check: {len(messages)} 条推送, user={uid}")
        for msg in messages:
            send_wework_message(uid, msg)
        _log(f"[system_action] nudge_check 完成, user={uid}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "sent": len(messages)}

    if action == "monthly_review":
        from skills.monthly_review import execute as monthly_execute
        state = read_state_cached(ctx) or {}
        _log(f"[system_action] monthly_review: 开始生成月度回顾, user={uid}")
        result = monthly_execute(data, state, ctx)
        write_state_and_update_cache(state, ctx)
        reply = result.get("reply") if result else None
        if reply:
            send_wework_message(uid, reply)
        _log(f"[system_action] monthly_review 完成, user={uid}, has_reply={bool(reply)}, 耗时={time.time()-t0:.1f}s")
        return {"ok": True, "has_reply": bool(reply)}

    if action == "companion_check":
        message = _run_companion_check(ctx)
        _log(f"[system_action] companion_check 完成, user={uid}, has_message={bool(message)}, 耗时={time.time()-t0:.1f}s")
        if message:
            send_wework_message(uid, message)
        return {"ok": True, "sent": 1 if message else 0}

    _log(f"[system_action] 未知 action: {action}")
    return {"ok": False, "error": f"unknown action: {action}"}


@app.route('/', methods=['GET'])
def health():
    """健康检查"""
    return "Karvis is alive"


# ============ 时间胶囊辅助函数 ============

def _build_time_capsule(ctx):
    """
    F3: 读取历史同日的笔记，供 morning_report 注入。
    返回 dict: {"7d_ago": {...}, "30d_ago": {...}, "365d_ago": {...}}
    """
    from concurrent.futures import ThreadPoolExecutor

    today = datetime.now(BEIJING_TZ).date()
    offsets = {
        "7d_ago": 7,
        "30d_ago": 30,
        "365d_ago": 365,
    }

    capsule = {}
    files_to_read = {}

    for key, days in offsets.items():
        past_date = today - timedelta(days=days)
        date_str = past_date.strftime("%Y-%m-%d")
        files_to_read[f"{key}_daily"] = (date_str, f"{ctx.daily_notes_dir}/{date_str}.md")

    # 也需要从 Quick-Notes 中提取历史日期条目
    files_to_read["quick_notes"] = (None, ctx.quick_notes_file)

    # 并发读取
    results = {}
    try:
        from brain import _executor
        executor = _executor
    except ImportError:
        executor = ThreadPoolExecutor(max_workers=4)

    futures = {k: executor.submit(OneDriveIO.read_text, v[1]) for k, v in files_to_read.items()}

    for k, fut in futures.items():
        try:
            results[k] = fut.result(timeout=15) or ""
        except Exception:
            results[k] = ""

    qn_text = results.get("quick_notes", "")

    for key, days in offsets.items():
        past_date = today - timedelta(days=days)
        date_str = past_date.strftime("%Y-%m-%d")

        daily_content = results.get(f"{key}_daily", "")
        # 从 Quick-Notes 提取该日期条目
        qn_entries = _extract_date_entries_for_capsule(qn_text, date_str)

        content_parts = []
        if qn_entries:
            content_parts.append(qn_entries[:500])
        if daily_content:
            # 只取日报总结部分，不取原始记录
            if "## 📊 今日总结" in daily_content:
                summary_section = daily_content.split("## 📊 今日总结")[1]
                end_idx = summary_section.find("\n## ")
                if end_idx >= 0:
                    summary_section = summary_section[:end_idx]
                content_parts.append(summary_section.strip()[:500])

        if content_parts:
            capsule[key] = {
                "date": date_str,
                "notes": "\n\n".join(content_parts)[:800]
            }
        else:
            capsule[key] = None

    return capsule


def _extract_date_entries_for_capsule(text, date_str):
    """从 Quick-Notes 中提取指定日期的条目（时间胶囊用）"""
    if not text:
        return ""
    entries = []
    sections = text.split("\n## ")
    for section in sections[1:]:
        first_line = section.split("\n")[0].strip()
        if first_line.startswith(date_str):
            # 只取内容，不取时间戳头
            body = "\n".join(section.split("\n")[1:]).strip()
            if body and body != "---":
                entries.append(body)
    return "\n".join(entries[:5])  # 最多 5 条


# ============ F5: 轻推系统辅助函数 ============

def _build_nudge_context(ctx):
    """
    F5: 构建 nudge 上下文信号，注入 morning_report / evening_checkin 的 context。
    读取 state 中的 nudge_state + mood_scores，返回 dict。
    """
    from memory import read_state_cached
    state = read_state_cached(ctx) or {}

    nudge = state.get("nudge_state", {})
    mood_scores = state.get("mood_scores", [])

    # 昨天的情绪评分
    today = datetime.now(BEIJING_TZ).date()
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_mood = None
    for s in mood_scores:
        if s.get("date") == yesterday_str:
            yesterday_mood = {"score": s.get("score"), "label": s.get("label", "")}
            break

    # 连续记录天数
    streak = nudge.get("streak", 0)

    # 距上次消息的小时数
    last_msg_date = nudge.get("last_message_date", "")
    hours_since_last = None
    if last_msg_date:
        try:
            last_dt = datetime.strptime(last_msg_date, "%Y-%m-%d")
            last_dt = last_dt.replace(tzinfo=BEIJING_TZ)
            now = datetime.now(BEIJING_TZ)
            hours_since_last = round((now - last_dt).total_seconds() / 3600, 1)
        except Exception:
            pass

    # 需要跟进的人（距上次提到超过 7 天 + 之前有负面情绪记录）
    people_to_follow = []
    people_last = nudge.get("people_last_mentioned", {})
    for name, last_date_str in people_last.items():
        try:
            last_d = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            if (today - last_d).days >= 7:
                people_to_follow.append(name)
        except Exception:
            pass

    # 打卡统计
    checkin_stats = state.get("checkin_stats", {})

    return {
        "yesterday_mood": yesterday_mood,
        "streak": streak,
        "last_message_hours_ago": hours_since_last,
        "people_to_follow_up": people_to_follow,
        "checkin_streak": checkin_stats.get("streak", 0),
    }


def _run_nudge_check(ctx):
    """
    F5: 独立轻推检测（每天 14:00 执行）— 纯规则引擎，不走 LLM。
    返回要推送的消息列表。
    """
    from memory import read_state_cached
    state = read_state_cached(ctx) or {}

    nudge = state.get("nudge_state", {})
    mood_scores = state.get("mood_scores", [])
    messages = []

    today = datetime.now(BEIJING_TZ).date()
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # 场景1: 沉默检测 — 今天 14:00 之前无消息
    last_msg_date = nudge.get("last_message_date", "")
    if last_msg_date != today_str:
        messages.append("今天很安静呀，是忙还是累了？随时可以来聊两句~")

    # 场景2: 情绪跟进 — 昨天 mood_score ≤ 4
    for s in mood_scores:
        if s.get("date") == yesterday_str and s.get("score") is not None:
            if s["score"] <= 4:
                label = s.get("label", "")
                hint = f"（{label}）" if label else ""
                messages.append(f"昨天好像有点低落{hint}，今天好点了吗？")
            break

    # 场景3: 连续记录鼓励
    streak = nudge.get("streak", 0)
    if streak > 0 and streak % 7 == 0:
        messages.append(f"你已经连续记录 {streak} 天了！这个习惯太棒了 ✨")
    elif streak == 3:
        messages.append("连续记录 3 天了~坚持下去，会看到很棒的变化！")

    return messages


# ============ F2: 主动陪伴系统 ============

def _parse_companion_datetime(time_str):
    """解析 nudge_state 中的时间字符串，返回 datetime 或 None"""
    if not time_str:
        return None
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=BEIJING_TZ)
    except Exception:
        return None


def _run_companion_check(ctx):
    """
    F2: 每 2 小时执行一次的智能陪伴检查。
    核心原则: 有事才发，没事 return None 静默跳过。
    返回: 消息文本 或 None
    """
    from memory import read_state_cached, write_state_and_update_cache
    from config import (COMPANION_SILENT_HOURS, COMPANION_INTERVAL_HOURS,
                        COMPANION_MAX_DAILY, COMPANION_RECENT_HOURS)

    state = read_state_cached(ctx) or {}
    nudge = state.get("nudge_state", {})
    now = datetime.now(BEIJING_TZ)

    # ── 防骚扰层 ──

    # 安静时间双保险（cron 已排除 0-7，代码再兜底）
    if now.hour < 8:
        _log(f"[Companion] 安静时间({now.hour}:00), 跳过")
        return None

    # 最近 N 小时内有过互动 → 不需要主动关怀
    last_msg_time = _parse_companion_datetime(nudge.get("last_message_time"))
    if last_msg_time and (now - last_msg_time).total_seconds() < COMPANION_RECENT_HOURS * 3600:
        _log(f"[Companion] 近期有互动({nudge.get('last_message_time')}), 跳过")
        return None

    # 上次陪伴推送距今不足 N 小时 → 跳过
    last_companion = _parse_companion_datetime(nudge.get("last_companion_time"))
    if last_companion and (now - last_companion).total_seconds() < COMPANION_INTERVAL_HOURS * 3600:
        _log(f"[Companion] 推送间隔不足({nudge.get('last_companion_time')}), 跳过")
        return None

    # 今天已推送 ≥ N 次 → 停止
    companion_count = nudge.get("companion_count_today", 0)
    if companion_count >= COMPANION_MAX_DAILY:
        _log(f"[Companion] 今日已推送{companion_count}次, 达到上限, 跳过")
        return None

    # ── 信号收集 ──
    signals = []

    # 信号 1: 长时间沉默（超过 N 小时没消息）
    if last_msg_time:
        silent_hours = (now - last_msg_time).total_seconds() / 3600
        if silent_hours > COMPANION_SILENT_HOURS:
            signals.append({
                "type": "silence",
                "detail": f"已经 {silent_hours:.0f} 小时没消息"
            })

    # 信号 2: 待办提醒
    pending_todos = _check_pending_todos(ctx)
    if pending_todos:
        signals.append({
            "type": "todo_reminder",
            "detail": f"有 {len(pending_todos)} 个待办未完成",
            "items": pending_todos[:3]
        })

    # 信号 3: 情绪跟进（昨天情绪低落且今天还没跟进）
    yesterday_mood = nudge.get("yesterday_mood_score")
    mood_followed = nudge.get("mood_followed_today", False)
    if yesterday_mood and int(yesterday_mood) <= 4 and not mood_followed:
        signals.append({
            "type": "mood_followup",
            "detail": f"昨天情绪评分 {yesterday_mood}/10"
        })

    # ── 决策: 没有信号就静默 ──
    if not signals:
        _log(f"[Companion] 无触发信号, 静默跳过")
        return None

    _log(f"[Companion] 触发信号: {json.dumps(signals, ensure_ascii=False)[:200]}")

    # ── 收集上下文，生成关怀消息 ──
    context = _build_companion_context(state, ctx)
    message = _generate_companion_message(signals, context, state)

    if message:
        # 更新计数器
        nudge["last_companion_time"] = now.strftime("%Y-%m-%d %H:%M")
        nudge["companion_count_today"] = companion_count + 1
        if any(s["type"] == "mood_followup" for s in signals):
            nudge["mood_followed_today"] = True
        state["nudge_state"] = nudge
        write_state_and_update_cache(state, ctx)
        _log(f"[Companion] 消息已生成, 计数={companion_count + 1}")

    return message


def _build_companion_context(state, ctx):
    """
    F2: 为陪伴消息收集丰富上下文（memory + 速记 + 待办 + 近期对话）。
    并发读取，控制总耗时。
    """
    from concurrent.futures import ThreadPoolExecutor

    context = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "memory": executor.submit(OneDriveIO.read_text, ctx.memory_file),
            "quick_notes": executor.submit(OneDriveIO.read_text, ctx.quick_notes_file),
            "todo": executor.submit(OneDriveIO.read_text, ctx.todo_file),
        }
        for key, future in futures.items():
            try:
                content = future.result(timeout=5)
                if content:
                    if key == "quick_notes":
                        lines = content.strip().split('\n')
                        recent = lines[-20:] if len(lines) > 20 else lines
                        context[key] = '\n'.join(recent)
                    else:
                        context[key] = content
            except Exception as e:
                _log(f"[Companion] 读取 {key} 失败: {e}")

    # 近期对话（从 state 中取）
    recent_msgs = state.get("recent_messages", [])
    if recent_msgs:
        context["recent_messages"] = recent_msgs[-5:]

    return context


def _generate_companion_message(signals, context, state):
    """
    F2: 基于信号 + 上下文，调 Qwen Flash 生成自然的关怀消息。
    注入 soul + memory + 近期速记，让消息更有温度和个性。
    """
    import prompts as _prompts

    # 组装 system prompt
    system_parts = []

    # 1. Soul（人设）— 从 prompts 模块取
    system_parts.append(f"## 你的人设\n{_prompts.SOUL}")

    # 2. Memory（长期记忆）
    memory = context.get("memory", "")
    if memory:
        system_parts.append(f"## 你对用户的了解\n{memory}")

    # 3. 任务指令 — 从 prompts 模块取
    system_parts.append(_prompts.COMPANION_TASK)

    system_prompt = '\n\n'.join(system_parts)

    # 组装 user message
    user_parts = []

    # 触发信号
    signal_text = json.dumps(signals, ensure_ascii=False)
    user_parts.append(f"**触发信号**: {signal_text}")

    # 近期速记
    quick_notes = context.get("quick_notes", "")
    if quick_notes:
        user_parts.append(f"**近期速记**:\n{quick_notes}")

    # 待办列表
    todo = context.get("todo", "")
    if todo:
        user_parts.append(f"**待办清单**:\n{todo}")

    # 近期对话
    recent_msgs = context.get("recent_messages", [])
    if recent_msgs:
        msg_text = '\n'.join([f"- {m.get('role','')}: {m.get('text','')[:80]}"
                              for m in recent_msgs])
        user_parts.append(f"**最近对话**:\n{msg_text}")

    # 当前时间
    now = datetime.now(BEIJING_TZ)
    period = "上午" if now.hour < 12 else ("下午" if now.hour < 18 else "晚上")
    user_parts.append(f"**当前时间**: {now.strftime('%Y-%m-%d %H:%M')} {period}")

    user_message = '\n\n'.join(user_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    _log(f"[Companion] 调用 Flash 生成关怀消息, signals={len(signals)}")
    return brain.call_llm(messages, model_tier="flash", max_tokens=200,
                          temperature=0.7)


def _check_pending_todos(ctx):
    """F2: 从 Todo.md 读取未完成待办"""
    try:
        todo_content = OneDriveIO.read_text(ctx.todo_file)
        if not todo_content:
            return []
        pending = []
        for line in todo_content.split('\n'):
            line = line.strip()
            if line.startswith('- [ ]'):
                pending.append(line[5:].strip())
        return pending
    except Exception as e:
        _log(f"[Companion] 读取待办失败: {e}")
        return []


# ============ V3-F13: 天气信息流辅助函数 ============

def _build_weather_context():
    """
    V3-F13: 获取天气信息，供 morning_report 注入。
    使用心知天气 API（免费版），返回 dict 或空 dict。
    """
    if not WEATHER_API_KEY:
        return {}
    try:
        resp = requests.get(
            "https://api.seniverse.com/v3/weather/daily.json",
            params={
                "key": WEATHER_API_KEY,
                "location": WEATHER_CITY,
                "language": "zh-Hans",
                "unit": "c",
                "start": 0,
                "days": 1
            },
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()["results"][0]["daily"][0]
            weather = {
                "city": WEATHER_CITY,
                "weather_day": data.get("text_day", ""),
                "weather_night": data.get("text_night", ""),
                "high": data.get("high", ""),
                "low": data.get("low", ""),
            }
            _log(f"[Weather] {WEATHER_CITY}: {weather['weather_day']} {weather['low']}~{weather['high']}°C")
            return weather
        else:
            _log(f"[Weather] API 返回非 200: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        _log(f"[Weather] 获取天气失败: {e}")
    return {}


# ============ V8: 智能调度引擎 ============

def _add_minutes(time_str, minutes):
    """给 HH:MM 格式的时间加减分钟数，返回 HH:MM"""
    try:
        parts = time_str.split(":")
        total = int(parts[0]) * 60 + int(parts[1]) + minutes
        total = max(0, min(total, 1439))
        return f"{total // 60:02d}:{total % 60:02d}"
    except (ValueError, IndexError):
        return time_str


def _generate_daily_intents(state):
    """V8: 基于用户节奏画像动态生成当天触达意图队列"""
    sched = state.get("scheduler", {})
    rhythm = sched.get("user_rhythm", {})
    now = datetime.now(BEIJING_TZ)
    is_weekend = now.weekday() >= 5

    wake_time = rhythm.get("avg_wake_time", SCHEDULER_DEFAULT_WAKE)
    sleep_time = rhythm.get("avg_sleep_time", SCHEDULER_DEFAULT_SLEEP)

    if is_weekend:
        shift = rhythm.get("weekend_shift", SCHEDULER_WEEKEND_SHIFT)
        wake_time = _add_minutes(wake_time, shift)

    intents = [
        {
            "type": "morning_report",
            "earliest": wake_time,
            "latest": _add_minutes(wake_time, 150),
            "ideal": _add_minutes(wake_time, 30),
            "priority": "normal",
            "status": "pending"
        },
        {
            "type": "todo_remind",
            "earliest": _add_minutes(wake_time, 60),
            "latest": "18:00",
            "ideal": _add_minutes(wake_time, 90),
            "priority": "normal",
            "max_times": 2,
            "sent_count": 0,
            "status": "pending"
        },
        {
            "type": "companion",
            "earliest": _add_minutes(wake_time, 120),
            "latest": _add_minutes(sleep_time, -60),
            "ideal": None,
            "priority": "low",
            "max_times": 2,
            "sent_count": 0,
            "conditions": {"silent_hours": 4},
            "status": "pending"
        },
        {
            "type": "nudge_check",
            "earliest": "13:00",
            "latest": "15:00",
            "ideal": "14:00",
            "priority": "low",
            "status": "pending"
        },
        {
            "type": "evening_checkin",
            "earliest": _add_minutes(sleep_time, -120),
            "latest": _add_minutes(sleep_time, -30),
            "ideal": _add_minutes(sleep_time, -90),
            "priority": "normal",
            "status": "pending"
        },
        {
            "type": "daily_report",
            "earliest": _add_minutes(sleep_time, -90),
            "latest": _add_minutes(sleep_time, -15),
            "ideal": _add_minutes(sleep_time, -60),
            "priority": "normal",
            "status": "pending"
        },
    ]

    _log(f"[V8] 生成每日意图: wake={wake_time}, sleep={sleep_time}, "
         f"weekend={is_weekend}, intents={len(intents)}")
    return intents


def _daily_init(uid, ctx):
    """V8: 每日初始化（多用户版）— 生成当天意图队列 + 重置计数器"""
    from memory import read_state_cached, write_state_and_update_cache
    state = read_state_cached(ctx) or {}
    sched = state.setdefault("scheduler", {})
    now = datetime.now(BEIJING_TZ)
    today_str = now.strftime("%Y-%m-%d")

    if sched.get("_init_date") == today_str:
        _log(f"[V8][{uid}] daily_init 今天已执行，跳过")
        return {"skipped": True, "date": today_str}

    intents = _generate_daily_intents(state)

    # 过期意图标记 skipped（容器重启等场景）
    now_min = now.hour * 60 + now.minute
    for intent in intents:
        latest = intent.get("latest", "23:59")
        try:
            latest_min = int(latest.split(":")[0]) * 60 + int(latest.split(":")[1])
        except (ValueError, IndexError):
            continue
        if now_min > latest_min:
            intent["status"] = "skipped"
            intent["_skip_reason"] = f"初始化时已过期（now={now.strftime('%H:%M')} > latest={latest}）"
            _log(f"[V8][{uid}] 意图 {intent['type']} 已过期，标记 skipped")

    sched["intents"] = intents
    sched["_init_date"] = today_str
    sched["_push_count_today"] = 0
    sched["_last_push_time"] = None

    state["scheduler"] = sched
    write_state_and_update_cache(state, ctx)

    _log(f"[V8][{uid}] daily_init 完成: {len(intents)} 个意图已生成")
    return {"date": today_str, "intents_count": len(intents)}


def _scheduler_tick(uid, ctx):
    """V8: 每 30 分钟心跳（多用户版）— 检查到期意图并执行"""
    from memory import read_state_cached, write_state_and_update_cache
    state = read_state_cached(ctx) or {}
    sched = state.setdefault("scheduler", {})
    now = datetime.now(BEIJING_TZ)
    now_str = now.strftime("%H:%M")
    today_str = now.strftime("%Y-%m-%d")

    # 兜底初始化
    if sched.get("_init_date") != today_str:
        _log(f"[V8][{uid}] tick 检测到未初始化，触发 daily_init")
        _daily_init(uid, ctx)
        state = read_state_cached(ctx) or {}
        sched = state.get("scheduler", {})

    intents = sched.get("intents", [])
    pending = [i for i in intents if i.get("status") == "pending"]

    if not pending:
        _log(f"[V8][{uid}] tick: 无 pending 意图")
        return {"evaluated": 0, "executed": 0}

    push_count = sched.get("_push_count_today", 0)
    if push_count >= SCHEDULER_PUSH_MAX_DAILY:
        _log(f"[V8][{uid}] tick: 今日推送已达上限 {push_count}/{SCHEDULER_PUSH_MAX_DAILY}")
        return {"evaluated": len(pending), "executed": 0, "reason": "daily_limit"}

    last_push = sched.get("_last_push_time")
    if last_push:
        try:
            last_parts = last_push.split(":")
            last_min = int(last_parts[0]) * 60 + int(last_parts[1])
            now_min = now.hour * 60 + now.minute
            if now_min - last_min < SCHEDULER_MIN_PUSH_GAP:
                _log(f"[V8][{uid}] tick: 距上次推送不足 {SCHEDULER_MIN_PUSH_GAP} 分钟，跳过")
                return {"evaluated": len(pending), "executed": 0, "reason": "min_gap"}
        except (ValueError, IndexError):
            pass

    ready = []
    for intent in pending:
        action = _rule_evaluate(intent, state, now)
        if action == "send":
            ready.append(intent)
        elif action == "skip":
            intent["status"] = "skipped"
            intent["_skip_reason"] = "rule_skip"

    if not ready:
        write_state_and_update_cache(state, ctx)
        _log(f"[V8][{uid}] tick: 评估 {len(pending)} 个意图，无需执行")
        return {"evaluated": len(pending), "executed": 0}

    if len(ready) > 1:
        ready = _try_merge_intents(ready)

    executed = 0
    for intent in ready:
        if push_count + executed >= SCHEDULER_PUSH_MAX_DAILY:
            break
        try:
            _execute_intent(intent, uid)
            intent["status"] = "sent"
            intent["_sent_at"] = now_str
            executed += 1
        except Exception as e:
            _log(f"[V8][{uid}] 意图执行失败 {intent['type']}: {e}")
            intent["_error"] = str(e)

    sched["_push_count_today"] = push_count + executed
    if executed > 0:
        sched["_last_push_time"] = now_str

    write_state_and_update_cache(state, ctx)
    _log(f"[V8][{uid}] tick 完成: 评估 {len(pending)}, 执行 {executed}")
    return {"evaluated": len(pending), "executed": executed}


def _rule_evaluate(intent, state, now):
    """V8 Layer 1: 规则引擎 — 返回 "send" | "skip" | "wait" """
    intent_type = intent.get("type", "")
    now_min = now.hour * 60 + now.minute

    earliest = intent.get("earliest", "00:00")
    latest = intent.get("latest", "23:59")
    ideal = intent.get("ideal")

    try:
        earliest_min = int(earliest.split(":")[0]) * 60 + int(earliest.split(":")[1])
        latest_min = int(latest.split(":")[0]) * 60 + int(latest.split(":")[1])
        ideal_min = None
        if ideal:
            ideal_min = int(ideal.split(":")[0]) * 60 + int(ideal.split(":")[1])
    except (ValueError, IndexError):
        return "wait"

    if now_min < earliest_min:
        return "wait"

    if now_min >= latest_min:
        intent["_trigger_reason"] = "兜底触发（已到 latest）"
        return "send"

    sched = state.get("scheduler", {})
    rhythm = sched.get("user_rhythm", {})
    avg_wake = rhythm.get("avg_wake_time", SCHEDULER_DEFAULT_WAKE)
    try:
        wake_min = int(avg_wake.split(":")[0]) * 60 + int(avg_wake.split(":")[1])
        if now_min < wake_min:
            return "wait"
    except (ValueError, IndexError):
        pass

    if intent_type in ("companion", "nudge_check"):
        nudge = state.get("nudge_state", {})
        last_msg = nudge.get("last_message_time", "")
        if last_msg:
            try:
                last_dt = datetime.strptime(last_msg, "%Y-%m-%d %H:%M")
                last_dt = last_dt.replace(tzinfo=BEIJING_TZ)
                if (now - last_dt).total_seconds() < 1800:
                    return "wait"
            except Exception:
                pass

    if intent_type == "companion":
        conditions = intent.get("conditions", {})
        silent_hours = conditions.get("silent_hours", 4)
        nudge = state.get("nudge_state", {})
        last_msg = nudge.get("last_message_time", "")
        if last_msg:
            try:
                last_dt = datetime.strptime(last_msg, "%Y-%m-%d %H:%M")
                last_dt = last_dt.replace(tzinfo=BEIJING_TZ)
                hours_silent = (now - last_dt).total_seconds() / 3600
                if hours_silent < silent_hours:
                    return "wait"
            except Exception:
                pass

    max_times = intent.get("max_times")
    if max_times and intent.get("sent_count", 0) >= max_times:
        return "skip"

    if ideal_min and now_min >= ideal_min:
        intent["_trigger_reason"] = "到达 ideal 时间"
        return "send"

    if not ideal_min:
        if intent_type == "companion":
            intent["_trigger_reason"] = "沉默条件满足"
            return "send"
        return "wait"

    return "wait"


_MERGEABLE = {
    ("evening_checkin", "daily_report"),
    ("morning_report", "todo_remind"),
}


def _try_merge_intents(intents):
    """V8: 尝试合并相近的意图"""
    types = set(i["type"] for i in intents)
    consumed = set()
    for pair in _MERGEABLE:
        if pair[0] in types and pair[1] in types:
            consumed.add(pair[1])

    merged = []
    for intent in intents:
        if intent["type"] in consumed:
            intent["status"] = "merged"
            _log(f"[V8] 意图合并: {intent['type']} 被合并")
        else:
            merged.append(intent)
    return merged


def _execute_intent(intent, user_id=None):
    """V8: 分发执行一个到期意图 — 通过 /system 端点"""
    intent_type = intent.get("type", "")
    _log(f"[V8] 执行意图: {intent_type}, user={user_id}, reason={intent.get('_trigger_reason', 'N/A')}")

    action_map = {
        "morning_report": "morning_report",
        "todo_remind": "todo_remind",
        "companion": "companion_check",
        "nudge_check": "nudge_check",
        "evening_checkin": "evening_checkin",
        "daily_report": "daily_report",
    }

    action = action_map.get(intent_type)
    if not action:
        _log(f"[V8] 未知意图类型: {intent_type}")
        return

    try:
        payload = {"action": action}
        if user_id:
            payload["user_id"] = user_id
        requests.post(
            f"http://127.0.0.1:{SERVER_PORT}/system",
            json=payload,
            timeout=120
        )
    except Exception as e:
        _log(f"[V8] 意图执行失败 {intent_type}: {e}")
        raise


# ============ V8: APScheduler 内嵌定时调度（心跳驱动） ============

def _setup_builtin_scheduler():
    """V8: 内嵌定时调度器 — 心跳驱动 + 少量固定任务

    改前（10 个独立 cron）→ 改后（4 个固定 + 1 心跳 + 1 每日初始化）：
    - 保留：refresh_cache / mood_generate / periodic_tasks(含 weekly/monthly)
    - 新增：scheduler_tick（每 30 分钟心跳评估）/ daily_init（05:00 生成意图队列）
    - 移除：morning_report / todo_remind / nudge_check / companion_check / evening_checkin / daily_report
            → 全部由 scheduler_tick 智能驱动
    """
    if os.environ.get("SCF_RUNTIME") or os.environ.get("TENCENTCLOUD_RUNENV"):
        _log("[Scheduler] 检测到 SCF 环境，跳过内置调度器")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        _log("[Scheduler] 未安装 apscheduler，跳过内置调度器。如需定时任务请: pip install apscheduler")
        return

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    def _fire_system_action(action):
        """通过 HTTP 调用自身 /system 端点（不传 user_id，遍历所有活跃用户）"""
        try:
            url = f"http://127.0.0.1:{SERVER_PORT}/system"
            resp = requests.post(url, json={"action": action}, timeout=600)
            _log(f"[Scheduler] {action} -> {resp.status_code}")
        except Exception as e:
            _log(f"[Scheduler] {action} 失败: {e}")

    jobs = [
        # 保留：不依赖用户节奏的固定任务
        ("refresh_cache",   {"trigger": "interval", "minutes": 30}),
        ("mood_generate",   {"trigger": "cron", "hour": 22, "minute": 0}),
        ("weekly_review",   {"trigger": "cron", "day_of_week": "sun", "hour": 21, "minute": 30}),
        ("monthly_review",  {"trigger": "cron", "day": "last", "hour": 22, "minute": 0}),

        # V8 新增：智能调度心跳
        ("scheduler_tick",  {"trigger": "interval", "minutes": SCHEDULER_TICK_MINUTES}),

        # V8 新增：每日意图初始化
        ("daily_init",      {"trigger": "cron", "hour": 5, "minute": 0}),
    ]

    for action, kwargs in jobs:
        scheduler.add_job(
            _fire_system_action, args=[action],
            id=action, max_instances=1,
            misfire_grace_time=300,
            **kwargs
        )

    scheduler.start()
    _log(f"[Scheduler][V8] 已启动 {len(jobs)} 个任务 "
         f"(心跳={SCHEDULER_TICK_MINUTES}min, 固定=4, 每日初始化=05:00)")

    # 启动时兜底触发一次 daily_init
    threading.Thread(target=lambda: _fire_system_action("daily_init"), daemon=True).start()


# ============ 启动初始化 ============

def _init_system_dirs():
    """启动时确保系统级目录存在"""
    os.makedirs(SYSTEM_DIR, exist_ok=True)
    _log(f"[Init] 系统目录已就绪: {SYSTEM_DIR}")


if __name__ == '__main__':
    _init_system_dirs()
    _setup_builtin_scheduler()
    app.run(host='0.0.0.0', port=SERVER_PORT, threaded=True)
