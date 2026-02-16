# -*- coding: utf-8 -*-
"""
KarvisForAll 用户上下文管理
每个用户请求携带 UserContext，封装该用户的所有路径和配置。
"""
import os
import sys
import json
import threading
from datetime import datetime, timezone, timedelta

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============ 系统级路径 ============
# DATA_DIR 是所有用户数据的根目录
_project_root = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_project_root, "data"))

SYSTEM_DIR = os.path.join(DATA_DIR, "_karvis_system")
USER_REGISTRY_FILE = os.path.join(SYSTEM_DIR, "users.json")
TOKENS_FILE = os.path.join(SYSTEM_DIR, "tokens.json")
USAGE_LOG_FILE = os.path.join(SYSTEM_DIR, "usage_log.jsonl")

# 不活跃天数阈值
INACTIVE_DAYS_THRESHOLD = int(os.environ.get("INACTIVE_DAYS_THRESHOLD", "7"))
# 每日消息上限
DAILY_MESSAGE_LIMIT = int(os.environ.get("DAILY_MESSAGE_LIMIT", "50"))


class UserContext:
    """每个用户请求携带的上下文，封装该用户的所有路径和配置"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.base_dir = os.path.join(DATA_DIR, "users", user_id)

        # 00-Inbox
        self.inbox_path = os.path.join(self.base_dir, "00-Inbox")
        self.quick_notes_file = os.path.join(self.inbox_path, "Quick-Notes.md")
        self.state_file = os.path.join(self.inbox_path, ".ai-life-state.json")
        self.todo_file = os.path.join(self.inbox_path, "Todo.md")
        self.attachments_path = os.path.join(self.inbox_path, "attachments")
        self.misc_file = os.path.join(self.inbox_path, "碎碎念.md")

        # 01-Daily
        self.daily_notes_dir = os.path.join(self.base_dir, "01-Daily")

        # 02-Notes 各分类
        _notes = os.path.join(self.base_dir, "02-Notes")
        self.book_notes_dir = os.path.join(_notes, "读书笔记")
        self.media_notes_dir = os.path.join(_notes, "影视笔记")
        self.work_notes_dir = os.path.join(_notes, "工作笔记")
        self.emotion_notes_dir = os.path.join(_notes, "情感日记")
        self.fun_notes_dir = os.path.join(_notes, "生活趣事")
        self.voice_journal_dir = os.path.join(_notes, "语音日记")

        # _Karvis 系统文件
        _karvis = os.path.join(self.base_dir, "_Karvis")
        self.memory_file = os.path.join(_karvis, "memory", "memory.md")
        self.user_config_file = os.path.join(_karvis, "user_config.json")
        self.decision_log_file = os.path.join(_karvis, "logs", "decisions.jsonl")

    def get_user_config(self) -> dict:
        """读取用户配置"""
        try:
            if os.path.exists(self.user_config_file):
                with open(self.user_config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            _log(f"[UserContext] 读取 user_config 失败 {self.user_id}: {e}")
        return {}

    def save_user_config(self, config: dict):
        """保存用户配置"""
        try:
            os.makedirs(os.path.dirname(self.user_config_file), exist_ok=True)
            with open(self.user_config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log(f"[UserContext] 保存 user_config 失败 {self.user_id}: {e}")

    def get_nickname(self) -> str:
        return self.get_user_config().get("nickname", "")

    def get_soul_override(self) -> str:
        return self.get_user_config().get("soul_override", "")

    def all_dirs(self) -> list:
        """返回该用户需要创建的所有目录"""
        return [
            self.inbox_path,
            self.attachments_path,
            self.daily_notes_dir,
            self.book_notes_dir,
            self.media_notes_dir,
            self.work_notes_dir,
            self.emotion_notes_dir,
            self.fun_notes_dir,
            self.voice_journal_dir,
            os.path.dirname(self.memory_file),      # _Karvis/memory/
            os.path.dirname(self.decision_log_file), # _Karvis/logs/
        ]


# ============ 用户注册表管理 ============

_registry_lock = threading.Lock()


def _now_str() -> str:
    return datetime.now(_BEIJING_TZ).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")


def _read_registry() -> dict:
    """读取用户注册表"""
    try:
        if os.path.exists(USER_REGISTRY_FILE):
            with open(USER_REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        _log(f"[UserContext] 读取注册表失败: {e}")
    return {"users": {}}


def _write_registry(registry: dict):
    """写入用户注册表"""
    try:
        os.makedirs(os.path.dirname(USER_REGISTRY_FILE), exist_ok=True)
        with open(USER_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"[UserContext] 写入注册表失败: {e}")


def get_or_create_user(user_id: str) -> tuple:
    """
    获取或创建用户。
    返回 (UserContext, is_new_user: bool)
    """
    with _registry_lock:
        registry = _read_registry()
        is_new = user_id not in registry.get("users", {})

        ctx = UserContext(user_id)

        if is_new:
            # 创建目录结构
            _log(f"[UserContext] 新用户 {user_id}: 创建目录结构...")
            for d in ctx.all_dirs():
                os.makedirs(d, exist_ok=True)
            _log(f"[UserContext] 新用户 {user_id}: 创建 {len(ctx.all_dirs())} 个目录完成")

            # 创建默认文件
            _init_default_files(ctx)
            _log(f"[UserContext] 新用户 {user_id}: 默认文件初始化完成")

            # 写入注册表
            if "users" not in registry:
                registry["users"] = {}
            registry["users"][user_id] = {
                "created_at": _now_str(),
                "last_active": _now_str(),
                "nickname": "",
                "status": "active",
                "message_count_today": 0,
                "message_count_date": _today_str(),
                "total_messages": 0,
            }
            _write_registry(registry)
            _log(f"[UserContext] 新用户注册完成: {user_id}, base_dir={ctx.base_dir}")
        else:
            # 更新活跃时间
            user_data = registry["users"][user_id]
            user_data["last_active"] = _now_str()

            # 重置每日计数（如果跨天了）
            if user_data.get("message_count_date") != _today_str():
                user_data["message_count_today"] = 0
                user_data["message_count_date"] = _today_str()

            _write_registry(registry)

        return ctx, is_new


def _init_default_files(ctx: UserContext):
    """为新用户创建默认文件"""
    # Quick-Notes
    if not os.path.exists(ctx.quick_notes_file):
        with open(ctx.quick_notes_file, "w", encoding="utf-8") as f:
            f.write("# Quick Notes\n\n快速笔记，从微信同步。\n\n---\n\n")

    # Todo
    if not os.path.exists(ctx.todo_file):
        with open(ctx.todo_file, "w", encoding="utf-8") as f:
            f.write("# Todo\n\n")

    # State
    if not os.path.exists(ctx.state_file):
        with open(ctx.state_file, "w", encoding="utf-8") as f:
            json.dump({}, f)

    # Memory
    if not os.path.exists(ctx.memory_file):
        with open(ctx.memory_file, "w", encoding="utf-8") as f:
            f.write("# Memory\n\n")

    # User Config
    if not os.path.exists(ctx.user_config_file):
        ctx.save_user_config({
            "nickname": "",
            "soul_override": "",
            "info": {},
            "onboarding_step": 1,  # 引导阶段: 1=等昵称, 2=等第一条笔记, 3=等第一个待办, 0=完成
            "preferences": {
                "morning_report": True,
                "evening_checkin": True,
                "companion_enabled": True,
            },
        })


def increment_message_count(user_id: str) -> tuple:
    """
    增加用户今日消息计数。
    返回 (current_count, is_over_limit)
    """
    with _registry_lock:
        registry = _read_registry()
        user_data = registry.get("users", {}).get(user_id)
        if not user_data:
            _log(f"[increment_message_count] 用户 {user_id} 不在注册表中，跳过计数")
            return 0, False

        # 跨天重置
        if user_data.get("message_count_date") != _today_str():
            _log(f"[increment_message_count] 用户 {user_id} 跨天重置计数 "
                 f"(旧日期={user_data.get('message_count_date')}, 新日期={_today_str()})")
            user_data["message_count_today"] = 0
            user_data["message_count_date"] = _today_str()

        user_data["message_count_today"] = user_data.get("message_count_today", 0) + 1
        user_data["total_messages"] = user_data.get("total_messages", 0) + 1
        _write_registry(registry)

        count = user_data["message_count_today"]
        over = count > DAILY_MESSAGE_LIMIT
        _log(f"[increment_message_count] 用户 {user_id}: 今日第 {count} 条, "
             f"总计 {user_data['total_messages']} 条, 超限={over}")
        return count, over


def get_all_active_users() -> list:
    """获取所有活跃用户 ID（定时任务用）"""
    registry = _read_registry()
    active = []
    now = datetime.now(_BEIJING_TZ)

    for uid, data in registry.get("users", {}).items():
        if data.get("status") != "active":
            _log(f"[get_all_active_users] 跳过非活跃用户: {uid} (status={data.get('status')})")
            continue
        # 检查活跃度
        last_active_str = data.get("last_active", "")
        try:
            last_active = datetime.fromisoformat(last_active_str)
            days_inactive = (now - last_active).days
            if days_inactive <= INACTIVE_DAYS_THRESHOLD:
                active.append(uid)
            else:
                _log(f"[get_all_active_users] 跳过不活跃用户: {uid} (不活跃 {days_inactive} 天)")
        except (ValueError, TypeError):
            # 解析失败的也包含进去（宽容策略）
            active.append(uid)

    _log(f"[get_all_active_users] 活跃用户: {active}")
    return active


def get_all_users() -> dict:
    """获取所有用户数据（管理员用）"""
    return _read_registry().get("users", {})


def update_user_status(user_id: str, status: str):
    """更新用户状态（active/suspended）"""
    with _registry_lock:
        registry = _read_registry()
        if user_id in registry.get("users", {}):
            registry["users"][user_id]["status"] = status
            _write_registry(registry)


def update_user_nickname(user_id: str, nickname: str):
    """更新注册表中的昵称"""
    with _registry_lock:
        registry = _read_registry()
        if user_id in registry.get("users", {}):
            registry["users"][user_id]["nickname"] = nickname
            _write_registry(registry)


def is_user_suspended(user_id: str) -> bool:
    """检查用户是否被挂起"""
    registry = _read_registry()
    user_data = registry.get("users", {}).get(user_id, {})
    return user_data.get("status") == "suspended"


# ============ Web 令牌管理 ============

import uuid

_tokens_lock = threading.Lock()


def _read_tokens() -> dict:
    """读取令牌表"""
    try:
        if os.path.exists(TOKENS_FILE):
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        _log(f"[Tokens] 读取令牌表失败: {e}")
    return {"tokens": {}}


def _write_tokens(data: dict):
    """写入令牌表"""
    try:
        os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"[Tokens] 写入令牌表失败: {e}")


def generate_token(user_id: str, expire_hours: int = 24) -> str:
    """
    为用户生成 Web 访问令牌。
    返回 token 字符串。
    """
    from config import WEB_TOKEN_EXPIRE_HOURS
    if expire_hours == 24:
        expire_hours = WEB_TOKEN_EXPIRE_HOURS

    token = str(uuid.uuid4())
    now = datetime.now(_BEIJING_TZ)
    expire_at = now + timedelta(hours=expire_hours)

    with _tokens_lock:
        data = _read_tokens()
        data["tokens"][token] = {
            "user_id": user_id,
            "created_at": now.isoformat(timespec="seconds"),
            "expire_at": expire_at.isoformat(timespec="seconds"),
        }
        _write_tokens(data)

    _log(f"[Tokens] 生成令牌: user={user_id}, token={token[:8]}..., "
         f"expire={expire_at.isoformat(timespec='seconds')}")
    return token


def verify_token(token: str) -> dict:
    """
    验证令牌。
    返回 {"valid": True, "user_id": "xxx"} 或 {"valid": False}
    """
    if not token:
        return {"valid": False}

    with _tokens_lock:
        data = _read_tokens()
        token_data = data.get("tokens", {}).get(token)

    if not token_data:
        _log(f"[Tokens] 令牌不存在: {token[:8]}...")
        return {"valid": False}

    # 检查过期
    try:
        expire_at = datetime.fromisoformat(token_data["expire_at"])
        now = datetime.now(_BEIJING_TZ)
        if now > expire_at:
            _log(f"[Tokens] 令牌已过期: {token[:8]}..., "
                 f"expire_at={token_data['expire_at']}")
            return {"valid": False, "expired": True}
    except (ValueError, KeyError):
        return {"valid": False}

    user_id = token_data.get("user_id", "")
    _log(f"[Tokens] 令牌验证成功: {token[:8]}... → user={user_id}")
    return {"valid": True, "user_id": user_id}


def cleanup_expired_tokens():
    """清理过期令牌"""
    now = datetime.now(_BEIJING_TZ)
    removed = 0

    with _tokens_lock:
        data = _read_tokens()
        tokens = data.get("tokens", {})
        to_remove = []

        for token, info in tokens.items():
            try:
                expire_at = datetime.fromisoformat(info["expire_at"])
                if now > expire_at:
                    to_remove.append(token)
            except (ValueError, KeyError):
                to_remove.append(token)

        for token in to_remove:
            del tokens[token]
            removed += 1

        if removed > 0:
            _write_tokens(data)

    if removed > 0:
        _log(f"[Tokens] 清理过期令牌: {removed} 个")
    return removed
