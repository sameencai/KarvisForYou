# -*- coding: utf-8 -*-
"""
KarvisForAll Web 路由 — API 接口 + 页面路由
所有 /web/* 和 /api/* 路由在此注册。
"""
import os
import sys
import json
import re
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, send_from_directory, redirect, url_for

from user_context import (
    verify_token, UserContext, get_all_users, update_user_status,
    SYSTEM_DIR, USAGE_LOG_FILE,
)
from config import ADMIN_TOKEN

_BEIJING_TZ = timezone(timedelta(hours=8))

web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__)


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============================================================
# 鉴权中间件
# ============================================================

def _get_token_from_request():
    """从请求中提取 token（Header > Cookie > query param）"""
    token = request.headers.get("X-Token", "")
    if not token:
        token = request.cookies.get("karvis_token", "")
    if not token:
        token = request.args.get("token", "")
    return token


def require_auth(f):
    """用户鉴权装饰器：验证 token 并注入 user_id"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _get_token_from_request()
        result = verify_token(token)
        if not result.get("valid"):
            _log(f"[WebAPI] 鉴权失败: token={token[:8] if token else 'empty'}..., "
                 f"expired={result.get('expired', False)}")
            return jsonify({"error": "令牌无效或已过期，请在企微中对 Karvis 说「给我查看链接」重新获取",
                            "expired": result.get("expired", False)}), 401
        kwargs["user_id"] = result["user_id"]
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """管理员鉴权装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        if not token:
            token = request.args.get("admin_token", "")
        if not token:
            token = request.cookies.get("karvis_admin_token", "")
        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            _log(f"[WebAPI] 管理员鉴权失败")
            return jsonify({"error": "管理员令牌无效"}), 403
        return f(*args, **kwargs)
    return decorated


def _get_ctx(user_id):
    """快速获取用户上下文（不创建目录）"""
    return UserContext(user_id)


# ============================================================
# 用户 API — /api/*
# ============================================================

@api_bp.route("/auth/verify", methods=["POST"])
def api_auth_verify():
    """POST /api/auth/verify — 验证令牌"""
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token", "") or _get_token_from_request()
    result = verify_token(token)

    if not result.get("valid"):
        _log(f"[WebAPI] /api/auth/verify 失败: token={token[:8] if token else 'empty'}...")
        return jsonify({"valid": False, "expired": result.get("expired", False)})

    user_id = result["user_id"]
    ctx = _get_ctx(user_id)
    nickname = ctx.get_nickname() or user_id

    _log(f"[WebAPI] /api/auth/verify 成功: user={user_id}, nickname={nickname}")
    return jsonify({"valid": True, "user_id": user_id, "nickname": nickname})


@api_bp.route("/dashboard", methods=["GET"])
@require_auth
def api_dashboard(user_id=None):
    """GET /api/dashboard — 仪表盘概览数据"""
    _log(f"[WebAPI] /api/dashboard: user={user_id}")
    ctx = _get_ctx(user_id)

    result = {
        "nickname": ctx.get_nickname() or user_id,
        "date": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
            datetime.now(_BEIJING_TZ).weekday()
        ],
    }

    # 速记统计
    try:
        qn = _read_file_safe(ctx.quick_notes_file)
        today_str = datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")
        today_notes = [s for s in qn.split("\n## ")[1:] if s.strip().startswith(today_str)]
        result["note_count_today"] = len(today_notes)
        # 最近 3 条预览
        recent = qn.split("\n## ")[1:4] if qn else []
        result["recent_notes"] = [s.strip()[:200] for s in recent]
    except Exception as e:
        _log(f"[WebAPI] dashboard 读取速记失败: {e}")
        result["note_count_today"] = 0
        result["recent_notes"] = []

    # 待办统计
    try:
        todo = _read_file_safe(ctx.todo_file)
        pending = len([l for l in todo.split('\n') if l.strip().startswith('- [ ]')])
        done = len([l for l in todo.split('\n') if l.strip().startswith('- [x]')])
        result["todo_pending"] = pending
        result["todo_done"] = done
        result["todo_total"] = pending + done
    except Exception:
        result["todo_pending"] = 0
        result["todo_done"] = 0
        result["todo_total"] = 0

    # 情绪曲线（最近 7 天）
    try:
        state = _read_state_safe(ctx)
        scores = state.get("mood_scores", [])
        result["mood_chart"] = scores[-7:] if scores else []
    except Exception:
        result["mood_chart"] = []

    # 打卡连续天数
    try:
        state = state if "state" in dir() else _read_state_safe(ctx)
        nudge = state.get("nudge_state", {})
        result["streak"] = nudge.get("streak", 0)
    except Exception:
        result["streak"] = 0

    # 最新日记
    try:
        daily_files = _list_files_safe(ctx.daily_notes_dir, "*.md")
        if daily_files:
            latest = sorted(daily_files, reverse=True)[0]
            result["latest_daily"] = {
                "file": latest,
                "date": latest.replace(".md", ""),
            }
        else:
            result["latest_daily"] = None
    except Exception:
        result["latest_daily"] = None

    return jsonify(result)


@api_bp.route("/notes", methods=["GET"])
@require_auth
def api_notes(user_id=None):
    """GET /api/notes — 获取速记"""
    _log(f"[WebAPI] /api/notes: user={user_id}")
    ctx = _get_ctx(user_id)
    date_filter = request.args.get("date", "")

    qn = _read_file_safe(ctx.quick_notes_file)
    if not qn:
        return jsonify({"notes": [], "has_more": False})

    # 按 ## 分割为条目
    sections = qn.split("\n## ")[1:]  # 跳过文件头部
    notes = []
    for s in sections:
        lines = s.strip().split("\n")
        if not lines:
            continue
        header = lines[0].strip()
        content = "\n".join(lines[1:]).strip()
        if content == "---":
            content = ""

        # 日期筛选
        if date_filter and not header.startswith(date_filter):
            continue

        notes.append({
            "time": header,
            "content": content,
        })

    # 最新在前
    notes.reverse()
    # 分页（简单截断）
    limit = int(request.args.get("limit", "50"))
    offset = int(request.args.get("offset", "0"))
    total = len(notes)
    page = notes[offset:offset + limit]

    return jsonify({
        "notes": page,
        "total": total,
        "has_more": (offset + limit) < total,
    })


@api_bp.route("/todos", methods=["GET"])
@require_auth
def api_todos(user_id=None):
    """GET /api/todos — 获取待办"""
    _log(f"[WebAPI] /api/todos: user={user_id}")
    ctx = _get_ctx(user_id)

    todo = _read_file_safe(ctx.todo_file)
    pending = []
    done = []

    for line in todo.split('\n'):
        line = line.strip()
        if line.startswith('- [ ]'):
            pending.append({"content": line[5:].strip(), "done": False})
        elif line.startswith('- [x]'):
            done.append({"content": line[5:].strip(), "done": True})

    return jsonify({"pending": pending, "done": done})


@api_bp.route("/daily", methods=["GET"])
@require_auth
def api_daily_list(user_id=None):
    """GET /api/daily — 获取日记/周报/月报列表"""
    _log(f"[WebAPI] /api/daily: user={user_id}")
    ctx = _get_ctx(user_id)

    reports = []
    files = _list_files_safe(ctx.daily_notes_dir, "*.md")
    for f in sorted(files, reverse=True):
        name = f.replace(".md", "")
        rtype = "daily"
        if name.startswith("周报"):
            rtype = "weekly"
        elif name.startswith("月报"):
            rtype = "monthly"
        elif name.startswith("情绪"):
            rtype = "mood"
        reports.append({
            "file": f,
            "date": name,
            "type": rtype,
            "title": name,
        })

    # 也查情绪日记目录
    emotion_files = _list_files_safe(ctx.emotion_notes_dir, "*.md")
    for f in sorted(emotion_files, reverse=True):
        name = f.replace(".md", "")
        reports.append({
            "file": f"emotion/{f}",
            "date": name,
            "type": "mood",
            "title": name,
        })

    return jsonify({"reports": reports})


@api_bp.route("/daily/<path:filename>", methods=["GET"])
@require_auth
def api_daily_detail(filename, user_id=None):
    """GET /api/daily/{filename} — 获取日记详情"""
    _log(f"[WebAPI] /api/daily/{filename}: user={user_id}")
    ctx = _get_ctx(user_id)

    # 安全检查：防止路径穿越
    filename = os.path.basename(filename)
    if not filename.endswith(".md"):
        filename += ".md"

    filepath = os.path.join(ctx.daily_notes_dir, filename)
    content = _read_file_safe(filepath)
    if not content:
        return jsonify({"error": "文件不存在"}), 404

    return jsonify({"content": content, "filename": filename})


@api_bp.route("/archive", methods=["GET"])
@require_auth
def api_archive_list(user_id=None):
    """GET /api/archive — 获取归档笔记列表"""
    _log(f"[WebAPI] /api/archive: user={user_id}")
    ctx = _get_ctx(user_id)
    category = request.args.get("category", "")

    categories = {
        "work": ctx.work_notes_dir,
        "emotion": ctx.emotion_notes_dir,
        "fun": ctx.fun_notes_dir,
        "book": ctx.book_notes_dir,
        "media": ctx.media_notes_dir,
        "voice": ctx.voice_journal_dir,
    }

    notes = []
    dirs_to_scan = {category: categories[category]} if category and category in categories else categories

    for cat, dir_path in dirs_to_scan.items():
        files = _list_files_safe(dir_path, "*.md")
        for f in sorted(files, reverse=True):
            # 读取第一行作为标题
            filepath = os.path.join(dir_path, f)
            first_line = _read_first_line(filepath)
            notes.append({
                "file": f,
                "category": cat,
                "title": first_line or f.replace(".md", ""),
                "date": _extract_date_from_filename(f),
            })

    return jsonify({"notes": notes})


@api_bp.route("/archive/<path:filename>", methods=["GET"])
@require_auth
def api_archive_detail(filename, user_id=None):
    """GET /api/archive/{filename} — 获取笔记详情"""
    _log(f"[WebAPI] /api/archive/{filename}: user={user_id}")
    ctx = _get_ctx(user_id)
    category = request.args.get("category", "")

    categories = {
        "work": ctx.work_notes_dir,
        "emotion": ctx.emotion_notes_dir,
        "fun": ctx.fun_notes_dir,
        "book": ctx.book_notes_dir,
        "media": ctx.media_notes_dir,
        "voice": ctx.voice_journal_dir,
    }

    # 安全检查
    filename = os.path.basename(filename)
    if not filename.endswith(".md"):
        filename += ".md"

    # 在指定或所有分类目录中查找
    search_dirs = [categories[category]] if category and category in categories else categories.values()

    for dir_path in search_dirs:
        filepath = os.path.join(dir_path, filename)
        content = _read_file_safe(filepath)
        if content:
            return jsonify({"content": content, "filename": filename})

    return jsonify({"error": "文件不存在"}), 404


@api_bp.route("/mood", methods=["GET"])
@require_auth
def api_mood(user_id=None):
    """GET /api/mood — 获取情绪数据"""
    _log(f"[WebAPI] /api/mood: user={user_id}")
    ctx = _get_ctx(user_id)

    state = _read_state_safe(ctx)
    scores = state.get("mood_scores", [])

    # 情绪日记列表
    diaries = []
    emotion_files = _list_files_safe(ctx.emotion_notes_dir, "*.md")
    for f in sorted(emotion_files, reverse=True):
        diaries.append({
            "file": f,
            "date": f.replace(".md", "").replace("情绪日记-", ""),
            "title": f.replace(".md", ""),
        })

    return jsonify({"scores": scores, "diaries": diaries})


@api_bp.route("/books", methods=["GET"])
@require_auth
def api_books(user_id=None):
    """GET /api/books — 获取读书笔记"""
    _log(f"[WebAPI] /api/books: user={user_id}")
    ctx = _get_ctx(user_id)

    books = []
    files = _list_files_safe(ctx.book_notes_dir, "*.md")
    for f in sorted(files, reverse=True):
        books.append({
            "file": f,
            "title": f.replace(".md", ""),
        })

    return jsonify({"books": books})


@api_bp.route("/media", methods=["GET"])
@require_auth
def api_media(user_id=None):
    """GET /api/media — 获取影视笔记"""
    _log(f"[WebAPI] /api/media: user={user_id}")
    ctx = _get_ctx(user_id)

    items = []
    files = _list_files_safe(ctx.media_notes_dir, "*.md")
    for f in sorted(files, reverse=True):
        items.append({
            "file": f,
            "title": f.replace(".md", ""),
        })

    return jsonify({"items": items})


# ============================================================
# 管理员 API — /api/admin/*
# ============================================================

@api_bp.route("/admin/users", methods=["GET"])
@require_admin
def api_admin_users():
    """GET /api/admin/users — 用户列表"""
    _log(f"[WebAPI] /api/admin/users")
    users = get_all_users()
    return jsonify({"users": users})


@api_bp.route("/admin/usage", methods=["GET"])
@require_admin
def api_admin_usage():
    """GET /api/admin/usage — LLM 用量统计"""
    _log(f"[WebAPI] /api/admin/usage")

    # 读取 usage_log.jsonl
    entries = []
    try:
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
    except Exception as e:
        _log(f"[WebAPI] 读取用量日志失败: {e}")

    # 按用户汇总
    user_stats = {}
    total_tokens = 0
    for entry in entries:
        uid = entry.get("user_id", "unknown")
        tokens = entry.get("total_tokens", 0)
        total_tokens += tokens

        if uid not in user_stats:
            user_stats[uid] = {"total_tokens": 0, "call_count": 0, "models": {}}
        user_stats[uid]["total_tokens"] += tokens
        user_stats[uid]["call_count"] += 1

        model = entry.get("model", "unknown")
        if model not in user_stats[uid]["models"]:
            user_stats[uid]["models"][model] = {"tokens": 0, "count": 0}
        user_stats[uid]["models"][model]["tokens"] += tokens
        user_stats[uid]["models"][model]["count"] += 1

    # 最近 7 天按日分组
    daily = {}
    for entry in entries:
        ts = entry.get("ts", "")[:10]  # YYYY-MM-DD
        if ts not in daily:
            daily[ts] = {"tokens": 0, "calls": 0}
        daily[ts]["tokens"] += entry.get("total_tokens", 0)
        daily[ts]["calls"] += 1

    return jsonify({
        "total_tokens": total_tokens,
        "total_calls": len(entries),
        "user_stats": user_stats,
        "daily": daily,
    })


@api_bp.route("/admin/users/<uid>/suspend", methods=["POST"])
@require_admin
def api_admin_suspend(uid):
    """POST /api/admin/users/{id}/suspend — 挂起用户"""
    _log(f"[WebAPI] /api/admin/suspend: {uid}")
    update_user_status(uid, "suspended")
    return jsonify({"ok": True, "user_id": uid, "status": "suspended"})


@api_bp.route("/admin/users/<uid>/activate", methods=["POST"])
@require_admin
def api_admin_activate(uid):
    """POST /api/admin/users/{id}/activate — 激活用户"""
    _log(f"[WebAPI] /api/admin/activate: {uid}")
    update_user_status(uid, "active")
    return jsonify({"ok": True, "user_id": uid, "status": "active"})


# ============================================================
# Web 页面路由 — /web/*（提供静态 HTML）
# ============================================================

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "web_static")


@web_bp.route("/")
def web_index():
    """Web 首页 → 重定向到登录页"""
    return redirect(url_for("web.web_login"))


@web_bp.route("/login")
def web_login():
    return _serve_page("login.html")


@web_bp.route("/dashboard")
def web_dashboard():
    return _serve_page("dashboard.html")


@web_bp.route("/notes")
def web_notes():
    return _serve_page("notes.html")


@web_bp.route("/todos")
def web_todos():
    return _serve_page("todos.html")


@web_bp.route("/daily")
def web_daily():
    return _serve_page("daily.html")


@web_bp.route("/archive")
def web_archive():
    return _serve_page("archive.html")


@web_bp.route("/mood")
def web_mood():
    return _serve_page("mood.html")


@web_bp.route("/admin")
def web_admin():
    return _serve_page("admin.html")


def _serve_page(filename):
    """提供静态 HTML 页面"""
    if not os.path.exists(os.path.join(_STATIC_DIR, filename)):
        return f"页面 {filename} 不存在", 404
    return send_from_directory(_STATIC_DIR, filename)


# ============================================================
# 工具函数
# ============================================================

def _read_file_safe(filepath):
    """安全读取文件，失败返回空字符串"""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        _log(f"[WebAPI] 读取文件失败 {filepath}: {e}")
    return ""


def _read_state_safe(ctx):
    """安全读取用户 state"""
    try:
        content = _read_file_safe(ctx.state_file)
        if content:
            return json.loads(content)
    except Exception as e:
        _log(f"[WebAPI] 读取 state 失败: {e}")
    return {}


def _list_files_safe(dir_path, pattern="*"):
    """安全列出目录下的文件"""
    try:
        if os.path.exists(dir_path):
            import glob
            files = glob.glob(os.path.join(dir_path, pattern))
            return [os.path.basename(f) for f in files if os.path.isfile(f)]
    except Exception as e:
        _log(f"[WebAPI] 列出文件失败 {dir_path}: {e}")
    return []


def _read_first_line(filepath):
    """读取文件第一行（去除 # 标记），用于获取标题"""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                if line.startswith("#"):
                    line = line.lstrip("#").strip()
                return line
    except Exception:
        pass
    return ""


def _extract_date_from_filename(filename):
    """从文件名中提取日期"""
    match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    return match.group(1) if match else ""
