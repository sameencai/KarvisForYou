# -*- coding: utf-8 -*-
"""
KarvisForAll V12 用户上下文管理
每个用户请求携带 UserContext，封装该用户的所有路径、IO 后端和配置。

V12 改造要点：
  1. 根据 user_config.storage_mode 路由 IO 后端（Local / OneDrive）
  2. OneDrive 用户使用远程路径体系，Local 用户使用本地路径体系
  3. 增加 Skill 过滤方法 (is_skill_allowed / get_allowed_skills)
  4. 增加 is_admin 属性
"""
import os
import sys
import json
import fnmatch
import threading
from datetime import datetime, timezone, timedelta

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    from logger import log
    log(msg)


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
    """每个用户请求携带的上下文，封装该用户的所有路径、IO 后端和配置。

    V12：根据 user_config.storage_mode 自动选择 LocalFileIO 或 OneDriveIO，
    并设置对应的路径体系。上层代码统一通过 ctx.IO.read_text(ctx.xxx_file) 访问。
    """

    def __init__(self, user_id: str):
        self.user_id = user_id

        # ---- 本地基础目录（所有用户都有，用于存放 user_config 等系统文件） ----
        self.base_dir = os.path.join(DATA_DIR, "users", user_id)
        _karvis_local = os.path.join(self.base_dir, "_Karvis")
        self.user_config_file = os.path.join(_karvis_local, "user_config.json")
        self.decision_log_file = os.path.join(_karvis_local, "logs", "decisions.jsonl")

        # ---- 加载用户配置 ----
        self.config = self._load_config()
        storage_mode = self.config.get("storage_mode", "local")

        # ---- 根据 storage_mode 初始化 IO 后端和路径体系 ----
        if storage_mode == "onedrive":
            self._init_onedrive_mode()
        else:
            self._init_local_mode()

        # ---- Skill 过滤配置 ----
        self._skills_config = self.config.get("skills", {})

    def _init_local_mode(self):
        """本地存储模式：IO = LocalFileIO，路径为本地文件系统路径"""
        from local_io import LocalFileIO
        self.IO = LocalFileIO
        self.storage_mode = "local"

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

        # _Karvis 系统文件（memory 走 IO，config/log 始终本地）
        self.memory_file = os.path.join(self.base_dir, "_Karvis", "memory", "memory.md")

        # 03-Finance（仅管理员可能使用，但路径先定义好）
        _finance = os.path.join(self.base_dir, "03-Finance")
        self.finance_dir = _finance
        self.finance_data_file = os.path.join(_finance, "finance_data.json")
        self.finance_inbox_dir = os.path.join(_finance, "inbox")
        self.finance_reports_dir = os.path.join(_finance, "reports")

    def _init_onedrive_mode(self):
        """OneDrive 存储模式：IO = OneDriveIO 实例，路径为 OneDrive 远程路径"""
        from storage import create_storage
        od_config = self.config.get("onedrive", {})
        self.IO = create_storage("onedrive", od_config)
        self.storage_mode = "onedrive"

        base = od_config.get("obsidian_base", "/应用/remotely-save/EmptyVault")

        # 00-Inbox
        self.inbox_path = f"{base}/00-Inbox"
        self.quick_notes_file = f"{base}/00-Inbox/Quick-Notes.md"
        self.state_file = f"{base}/00-Inbox/.ai-life-state.json"
        self.todo_file = f"{base}/00-Inbox/Todo.md"
        self.attachments_path = f"{base}/00-Inbox/attachments"
        self.misc_file = f"{base}/00-Inbox/碎碎念.md"

        # 01-Daily
        self.daily_notes_dir = f"{base}/01-Daily"

        # 02-Notes 各分类
        self.book_notes_dir = f"{base}/02-Notes/读书笔记"
        self.media_notes_dir = f"{base}/02-Notes/影视笔记"
        self.work_notes_dir = f"{base}/02-Notes/工作笔记"
        self.emotion_notes_dir = f"{base}/02-Notes/情感日记"
        self.fun_notes_dir = f"{base}/02-Notes/生活趣事"
        self.voice_journal_dir = f"{base}/02-Notes/语音日记"

        # _Karvis 系统文件
        self.memory_file = f"{base}/_Karvis/memory/memory.md"

        # 03-Finance
        self.finance_dir = f"{base}/03-Finance"
        self.finance_data_file = f"{base}/03-Finance/finance_data.json"
        self.finance_inbox_dir = f"{base}/03-Finance/inbox"
        self.finance_reports_dir = f"{base}/03-Finance/reports"

    def _load_config(self) -> dict:
        """从本地文件加载用户配置（user_config.json 始终存储在本地）"""
        try:
            if os.path.exists(self.user_config_file):
                with open(self.user_config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            _log(f"[UserContext] 读取 user_config 失败 {self.user_id}: {e}")
        return {}

    def get_user_config(self) -> dict:
        """读取用户配置（返回缓存的 self.config）"""
        return self.config

    def save_user_config(self, config: dict):
        """保存用户配置到本地文件，并更新内存缓存"""
        try:
            os.makedirs(os.path.dirname(self.user_config_file), exist_ok=True)
            with open(self.user_config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self.config = config
        except Exception as e:
            _log(f"[UserContext] 保存 user_config 失败 {self.user_id}: {e}")

    def get_nickname(self) -> str:
        return self.config.get("nickname", "")

    def get_soul_override(self) -> str:
        return self.config.get("soul_override", "")

    # ============ Skill 过滤 ============

    def _matches(self, skill_name: str, patterns: list) -> bool:
        """支持精确名与通配符（如 decision.*）"""
        return any(fnmatch.fnmatch(skill_name, p) for p in patterns)

    def is_skill_allowed(self, skill_name: str) -> bool:
        """检查该用户是否有权使用指定 Skill（不含 visibility 检查，visibility 由 skill_loader 处理）"""
        mode = self._skills_config.get("mode", "blacklist")
        skill_list = self._skills_config.get("list", [])

        if mode == "whitelist":
            return bool(skill_list) and self._matches(skill_name, skill_list)
        else:  # blacklist
            return not self._matches(skill_name, skill_list)

    def get_allowed_skills(self, all_skills: dict) -> dict:
        """从全量 Skill 元数据中过滤出该用户可用的"""
        return {k: v for k, v in all_skills.items() if self.is_skill_allowed(k)}

    @property
    def is_admin(self) -> bool:
        return self.config.get("role") == "admin"

    # ============ 目录创建 ============

    def all_dirs(self) -> list:
        """返回该用户需要创建的所有本地目录（仅 local 模式需要实际创建）"""
        base = self.base_dir
        inbox = os.path.join(base, "00-Inbox")
        _notes = os.path.join(base, "02-Notes")
        _karvis = os.path.join(base, "_Karvis")
        return [
            inbox,
            os.path.join(inbox, "attachments"),
            os.path.join(base, "01-Daily"),
            os.path.join(_notes, "读书笔记"),
            os.path.join(_notes, "影视笔记"),
            os.path.join(_notes, "工作笔记"),
            os.path.join(_notes, "情感日记"),
            os.path.join(_notes, "生活趣事"),
            os.path.join(_notes, "语音日记"),
            os.path.join(_karvis, "memory"),
            os.path.join(_karvis, "logs"),
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
    """为新用户创建默认文件（兼容 Local 和 OneDrive 模式）"""
    # Quick-Notes
    existing = ctx.IO.read_text(ctx.quick_notes_file)
    if not existing:
        ctx.IO.write_text(ctx.quick_notes_file, "# Quick Notes\n\n快速笔记，从微信同步。\n\n---\n\n")

    # Todo
    existing = ctx.IO.read_text(ctx.todo_file)
    if not existing:
        ctx.IO.write_text(ctx.todo_file, "# Todo\n\n")

    # State
    existing = ctx.IO.read_text(ctx.state_file)
    if not existing:
        ctx.IO.write_text(ctx.state_file, "{}")

    # Memory
    existing = ctx.IO.read_text(ctx.memory_file)
    if not existing:
        ctx.IO.write_text(ctx.memory_file, "")

    # User Config — V12: 增加 role / storage_mode / skills 字段; V13: channel
    if not os.path.exists(ctx.user_config_file):
        # 自动检测渠道
        channel = "telegram" if ctx.user_id.startswith("tg_") else "wework"
        config_data = {
            "nickname": "",
            "ai_name": "Karvis",
            "soul_override": "",
            "channel": channel,
            "role": "user",
            "storage_mode": "local",
            "onedrive": {},
            "skills": {
                "mode": "blacklist",
                "list": [],
            },
            "info": {},
            "onboarding_step": 1,  # 引导阶段: 1=等昵称, 2=等第一条笔记, 3=等第一个待办, 0=完成
            "preferences": {
                "morning_report": True,
                "evening_checkin": True,
                "companion_enabled": True,
            },
        }
        # Telegram 用户保存 chat_id
        if channel == "telegram":
            config_data["telegram_chat_id"] = ctx.user_id[3:]  # 去掉 tg_ 前缀
        ctx.save_user_config(config_data)


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
                data = json.load(f)
                if "tokens" not in data:
                    data["tokens"] = {}
                return data
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


# ============ 邀请码管理 ============

INVITE_CODES_FILE = os.path.join(SYSTEM_DIR, "invite_codes.json")
_invite_lock = threading.Lock()


def _read_invite_codes() -> list:
    """读取邀请码列表"""
    try:
        if os.path.exists(INVITE_CODES_FILE):
            with open(INVITE_CODES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("codes", [])
    except Exception as e:
        _log(f"[InviteCode] 读取失败: {e}")
    return []


def _write_invite_codes(codes: list):
    """写入邀请码列表"""
    try:
        os.makedirs(os.path.dirname(INVITE_CODES_FILE), exist_ok=True)
        with open(INVITE_CODES_FILE, "w", encoding="utf-8") as f:
            json.dump({"codes": codes}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"[InviteCode] 写入失败: {e}")


def create_invite_code(created_by: str = "admin") -> str:
    """生成一个 8 位邀请码"""
    import random
    import string
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    with _invite_lock:
        codes = _read_invite_codes()
        codes.append({
            "code": code,
            "created_at": _now_str(),
            "created_by": created_by,
            "used": False,
            "used_by": "",
            "used_at": "",
        })
        _write_invite_codes(codes)
    _log(f"[InviteCode] 生成邀请码: {code}")
    return code


def get_all_invite_codes() -> list:
    """获取所有邀请码"""
    return _read_invite_codes()


def use_invite_code(code: str, user_id: str) -> bool:
    """使用邀请码，成功返回 True"""
    with _invite_lock:
        codes = _read_invite_codes()
        for c in codes:
            if c["code"] == code and not c["used"]:
                c["used"] = True
                c["used_by"] = user_id
                c["used_at"] = _now_str()
                _write_invite_codes(codes)
                _log(f"[InviteCode] 邀请码 {code} 被 {user_id} 使用")
                return True
    return False


def delete_invite_code(code: str) -> bool:
    """删除邀请码"""
    with _invite_lock:
        codes = _read_invite_codes()
        new_codes = [c for c in codes if c["code"] != code]
        if len(new_codes) < len(codes):
            _write_invite_codes(new_codes)
            _log(f"[InviteCode] 删除邀请码: {code}")
            return True
    return False


# ============ 公告管理 ============

ANNOUNCEMENTS_FILE = os.path.join(SYSTEM_DIR, "announcements.json")
_announce_lock = threading.Lock()


def _read_announcements() -> list:
    try:
        if os.path.exists(ANNOUNCEMENTS_FILE):
            with open(ANNOUNCEMENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("announcements", [])
    except Exception as e:
        _log(f"[Announce] 读取失败: {e}")
    return []


def _write_announcements(announcements: list):
    try:
        os.makedirs(os.path.dirname(ANNOUNCEMENTS_FILE), exist_ok=True)
        with open(ANNOUNCEMENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"announcements": announcements}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"[Announce] 写入失败: {e}")


def create_announcement(title: str, content: str) -> dict:
    ann = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "content": content,
        "created_at": _now_str(),
    }
    with _announce_lock:
        anns = _read_announcements()
        anns.insert(0, ann)
        _write_announcements(anns)
    return ann


def get_announcements() -> list:
    return _read_announcements()


def delete_announcement(ann_id: str) -> bool:
    with _announce_lock:
        anns = _read_announcements()
        new_anns = [a for a in anns if a["id"] != ann_id]
        if len(new_anns) < len(anns):
            _write_announcements(new_anns)
            return True
    return False


# ============ 用户反馈管理 ============

FEEDBACKS_FILE = os.path.join(SYSTEM_DIR, "feedbacks.json")
_feedback_lock = threading.Lock()


def _read_feedbacks() -> list:
    try:
        if os.path.exists(FEEDBACKS_FILE):
            with open(FEEDBACKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("feedbacks", [])
    except Exception as e:
        _log(f"[Feedback] 读取失败: {e}")
    return []


def _write_feedbacks(feedbacks: list):
    try:
        os.makedirs(os.path.dirname(FEEDBACKS_FILE), exist_ok=True)
        with open(FEEDBACKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"feedbacks": feedbacks}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"[Feedback] 写入失败: {e}")


def create_feedback(user_id: str, content: str) -> dict:
    fb = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "content": content,
        "created_at": _now_str(),
        "reply": "",
        "replied_at": "",
    }
    with _feedback_lock:
        fbs = _read_feedbacks()
        fbs.insert(0, fb)
        _write_feedbacks(fbs)
    return fb


def get_feedbacks() -> list:
    return _read_feedbacks()


def reply_feedback(fb_id: str, reply: str) -> bool:
    with _feedback_lock:
        fbs = _read_feedbacks()
        for fb in fbs:
            if fb["id"] == fb_id:
                fb["reply"] = reply
                fb["replied_at"] = _now_str()
                _write_feedbacks(fbs)
                return True
    return False
