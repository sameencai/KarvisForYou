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
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, request, jsonify, send_from_directory, redirect, url_for

from user_context import (
    verify_token, UserContext, get_all_users, update_user_status,
    SYSTEM_DIR, USAGE_LOG_FILE, DATA_DIR,
    create_invite_code, get_all_invite_codes, delete_invite_code,
    create_announcement, get_announcements, delete_announcement,
    create_feedback, get_feedbacks, reply_feedback,
)
from config import ADMIN_TOKEN, LOG_FILE_KARVISFORALL

_BEIJING_TZ = timezone(timedelta(hours=8))

web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__)


def _log(msg):
    ts = datetime.now(_BEIJING_TZ).strftime("%H:%M:%S")
    print(f"{ts} {msg}", file=sys.stderr, flush=True)


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
    """GET /api/dashboard — 仪表盘概览数据（并行 IO）"""
    ctx = _get_ctx(user_id)
    now = datetime.now(_BEIJING_TZ)

    result = {
        "nickname": ctx.get_nickname() or user_id,
        "date": now.strftime("%Y-%m-%d"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
    }

    # ---- 并行读取所有数据源（5 次 IO → 1 次并行） ----
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_notes = pool.submit(_read_file_safe, ctx, ctx.quick_notes_file)
        f_todo = pool.submit(_read_file_safe, ctx, ctx.todo_file)
        f_state = pool.submit(_read_state_safe, ctx)
        f_daily = pool.submit(_list_files_safe, ctx, ctx.daily_notes_dir, "*.md")
        f_memory = pool.submit(_read_file_safe, ctx, ctx.memory_file)

    qn = f_notes.result()
    todo_raw = f_todo.result()
    state = f_state.result()
    daily_files = f_daily.result()
    mem_content = f_memory.result()

    # 速记统计
    today_str = now.strftime("%Y-%m-%d")
    try:
        today_notes = [s for s in qn.split("\n## ")[1:] if s.strip().startswith(today_str)]
        result["note_count_today"] = len(today_notes)
        recent = qn.split("\n## ")[1:4] if qn else []
        result["recent_notes"] = [s.strip()[:100] for s in recent]
    except Exception:
        result["note_count_today"] = 0
        result["recent_notes"] = []

    # 待办统计
    try:
        lines = todo_raw.split('\n')
        pending = len([l for l in lines if l.strip().startswith('- [ ]')])
        done = len([l for l in lines if l.strip().startswith('- [x]')])
        result["todo_pending"] = pending
        result["todo_done"] = done
        result["todo_total"] = pending + done
    except Exception:
        result["todo_pending"] = 0
        result["todo_done"] = 0
        result["todo_total"] = 0

    # 情绪曲线（最近 7 天）
    scores = state.get("mood_scores", [])
    result["mood_chart"] = scores[-7:] if scores else []

    # 打卡连续天数
    result["streak"] = state.get("nudge_state", {}).get("streak", 0)

    # 最新日记
    if daily_files:
        latest = sorted(daily_files, reverse=True)[0]
        result["latest_daily"] = {"file": latest, "date": latest.replace(".md", "")}
    else:
        result["latest_daily"] = None

    # 记忆概要
    if mem_content:
        try:
            mem_sections = [p for p in re.split(r'\n(?=## )', mem_content) if p.strip().startswith("## ")]
            mem_items = len([l for l in mem_content.split('\n') if l.strip().startswith("- ")])
            result["memory_summary"] = {"sections": len(mem_sections), "items": mem_items}
        except Exception:
            result["memory_summary"] = None
    else:
        result["memory_summary"] = None

    # ---- Dashboard 增强字段 ----
    try:
        result["daily_top3"] = state.get("daily_top3", [])
        result["active_book"] = state.get("active_book")
        result["active_media"] = state.get("active_media")
        result["pending_decisions_count"] = len(state.get("pending_decisions", []))
        result["reflect_streak"] = state.get("reflect_stats", {}).get("streak", 0)
        active_exp = state.get("active_experiment")
        if active_exp:
            prog = active_exp.get("progress", [])
            tgt = active_exp.get("target_days", 1)
            done_count = sum(1 for p in prog if p.get("done"))
            result["active_experiment"] = {
                "name": active_exp.get("name", ""),
                "progress": round(done_count / tgt * 100) if tgt > 0 else 0,
            }
        else:
            result["active_experiment"] = None
        result["role"] = ctx.config.get("role", "")
    except Exception:
        result["daily_top3"] = []
        result["active_book"] = None
        result["active_media"] = None
        result["pending_decisions_count"] = 0
        result["reflect_streak"] = 0
        result["active_experiment"] = None
        result["role"] = ""

    return jsonify(result)


@api_bp.route("/notes", methods=["GET"])
@require_auth
def api_notes(user_id=None):
    """GET /api/notes — 获取速记"""
    ctx = _get_ctx(user_id)
    date_filter = request.args.get("date", "")

    qn = _read_file_safe(ctx, ctx.quick_notes_file)
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
        # 去掉尾部的分隔线 ---
        if content.endswith("---"):
            content = content[:-3].strip()


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
    """GET /api/todos — 获取待办（含完整字段）"""
    ctx = _get_ctx(user_id)
    state = _read_state_safe(ctx)
    todos = state.get("todos", [])
    today = datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")

    todo_text = _read_file_safe(ctx, ctx.todo_file)
    from skills.todo_manage import _parse_todo_md
    doing, done_list = _parse_todo_md(todo_text)

    # ── 构建 pending 列表（保留 state 中的丰富字段） ──
    def _enrich(item, idx):
        """将 Todo.md 行 + state todo 合并为完整前端对象"""
        from skills.todo_manage import _find_todo_by_content
        t = _find_todo_by_content(todos, item["content"])
        rec = {
            "index": idx,
            "content": item["content"],
            "done": False,
        }
        if t:
            rec["id"] = t.get("id", "")
            rec["due_date"] = t.get("due_date", "")
            rec["remind_at"] = t.get("remind_at", "")
            rec["recur"] = t.get("recur", "")
            rec["recur_spec"] = t.get("recur_spec", {})
            rec["created"] = t.get("created", "")
            rec["last_completed"] = t.get("last_completed", "")
            rec["checkin_today"] = (t.get("recur") and t.get("last_completed") == today)
        return rec

    pending = [_enrich(d, i) for i, d in enumerate(doing) if d["raw"].strip().startswith("- [ ]")]
    # 循环待办今日已打卡也算 pending（打卡态）
    checkin = [_enrich(d, i) for i, d in enumerate(doing) if d["raw"].strip().startswith("- [x]")]
    for c in checkin:
        c["done"] = False
        c["checkin_today"] = True

    done = [{"content": d["content"], "done": True} for d in done_list]

    return jsonify({"pending": pending + checkin, "done": done})


@api_bp.route("/todos/complete", methods=["POST"])
@require_auth
def api_todo_complete(user_id=None):
    """POST /api/todos/complete — 完成待办"""
    ctx = _get_ctx(user_id)
    data = request.get_json(force=True, silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    index = data.get("index")  # 0-based 序号

    if not keyword and index is None:
        return jsonify({"ok": False, "error": "缺少 keyword 或 index"}), 400

    from skills.todo_manage import complete as todo_complete
    state = _read_state_safe(ctx)

    params = {}
    if index is not None:
        params["indices"] = str(int(index) + 1)  # 转为 1-based
    else:
        params["keyword"] = keyword

    result = todo_complete(params, state, ctx)

    if result.get("state_updates"):
        state.update(result["state_updates"])
        try:
            ctx.IO.write_json(ctx.state_file, state)
        except Exception as e:
            _log(f"[WebAPI] 写入 state 失败: {e}")

    return jsonify({
        "ok": result.get("success", False),
        "reply": result.get("reply", ""),
    })


@api_bp.route("/todos", methods=["POST"])
@require_auth
def api_todo_add(user_id=None):
    """POST /api/todos — 添加待办"""
    ctx = _get_ctx(user_id)
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "待办内容不能为空"}), 400

    from skills.todo_manage import add as todo_add
    state = _read_state_safe(ctx)

    params = {"content": content}
    if data.get("due_date"):
        params["due_date"] = data["due_date"]
    if data.get("remind_at"):
        params["remind_at"] = data["remind_at"]
    if data.get("recur"):
        params["recur"] = data["recur"]
    if data.get("recur_spec"):
        params["recur_spec"] = data["recur_spec"]

    result = todo_add(params, state, ctx)

    if result.get("state_updates"):
        state.update(result["state_updates"])
        try:
            ctx.IO.write_json(ctx.state_file, state)
        except Exception as e:
            _log(f"[WebAPI] 写入 state 失败: {e}")

    return jsonify({
        "ok": result.get("success", False),
        "reply": result.get("reply", ""),
    })


@api_bp.route("/todos", methods=["PUT"])
@require_auth
def api_todo_edit(user_id=None):
    """PUT /api/todos — 编辑待办"""
    ctx = _get_ctx(user_id)
    data = request.get_json(force=True, silent=True) or {}

    keyword = (data.get("keyword") or "").strip()
    index = data.get("index")  # 前端传 0-based

    if not keyword and index is None:
        return jsonify({"ok": False, "error": "缺少 keyword 或 index"}), 400

    from skills.todo_manage import edit as todo_edit
    state = _read_state_safe(ctx)

    params = {}
    if index is not None:
        params["index"] = int(index) + 1  # 转为 1-based
    if keyword:
        params["keyword"] = keyword

    # 转发可选修改字段
    for field in ("new_content", "new_due_date", "new_remind_at", "new_recur", "new_recur_spec"):
        if field in data:
            params[field] = data[field]

    result = todo_edit(params, state, ctx)

    if result.get("state_updates"):
        state.update(result["state_updates"])
        try:
            ctx.IO.write_json(ctx.state_file, state)
        except Exception as e:
            _log(f"[WebAPI] 写入 state 失败: {e}")

    return jsonify({
        "ok": result.get("success", False),
        "reply": result.get("reply", ""),
    })


@api_bp.route("/todos", methods=["DELETE"])
@require_auth
def api_todo_delete(user_id=None):
    """DELETE /api/todos — 删除待办"""
    ctx = _get_ctx(user_id)
    data = request.get_json(force=True, silent=True) or {}

    keyword = (data.get("keyword") or "").strip()
    index = data.get("index")  # 前端传 0-based

    if not keyword and index is None:
        return jsonify({"ok": False, "error": "缺少 keyword 或 index"}), 400

    from skills.todo_manage import delete as todo_delete
    state = _read_state_safe(ctx)

    params = {}
    if index is not None:
        params["indices"] = str(int(index) + 1)  # 转为 1-based
    if keyword:
        params["keyword"] = keyword

    result = todo_delete(params, state, ctx)

    if result.get("state_updates"):
        state.update(result["state_updates"])
        try:
            ctx.IO.write_json(ctx.state_file, state)
        except Exception as e:
            _log(f"[WebAPI] 写入 state 失败: {e}")

    return jsonify({
        "ok": result.get("success", False),
        "reply": result.get("reply", ""),
    })


@api_bp.route("/daily", methods=["GET"])
@require_auth
def api_daily_list(user_id=None):
    """GET /api/daily — 获取日记/周报/月报列表"""
    ctx = _get_ctx(user_id)

    reports = []
    files = _list_files_safe(ctx, ctx.daily_notes_dir, "*.md")
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
    emotion_files = _list_files_safe(ctx, ctx.emotion_notes_dir, "*.md")
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
    ctx = _get_ctx(user_id)

    # 检测 emotion/ 前缀，路由到情绪日记目录
    if filename.startswith("emotion/"):
        safe_name = os.path.basename(filename)
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        filepath = _join_path(ctx, ctx.emotion_notes_dir, safe_name)
    else:
        # 安全检查：防止路径穿越
        safe_name = os.path.basename(filename)
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        filepath = _join_path(ctx, ctx.daily_notes_dir, safe_name)

    content = _read_file_safe(ctx, filepath)
    if not content:
        return jsonify({"error": "文件不存在"}), 404

    return jsonify({"content": content, "filename": safe_name})


@api_bp.route("/archive", methods=["GET"])
@require_auth
def api_archive_list(user_id=None):
    """GET /api/archive — 获取归档笔记列表"""
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
        files = _list_files_safe(ctx, dir_path, "*.md")
        for f in sorted(files, reverse=True):
            # 读取第一行作为标题
            filepath = _join_path(ctx, dir_path, f)
            first_line = _read_first_line(ctx, filepath)
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
        filepath = _join_path(ctx, dir_path, filename)
        content = _read_file_safe(ctx, filepath)
        if content:
            return jsonify({"content": content, "filename": filename})

    return jsonify({"error": "文件不存在"}), 404


@api_bp.route("/mood", methods=["GET"])
@require_auth
def api_mood(user_id=None):
    """GET /api/mood — 获取情绪数据"""
    ctx = _get_ctx(user_id)

    state = _read_state_safe(ctx)
    scores = state.get("mood_scores", [])

    # 情绪日记列表
    diaries = []
    emotion_files = _list_files_safe(ctx, ctx.emotion_notes_dir, "*.md")
    for f in sorted(emotion_files, reverse=True):
        diaries.append({
            "file": f,
            "date": f.replace(".md", "").replace("情绪日记-", ""),
            "title": f.replace(".md", ""),
        })

    # 回填修复：如果 scores 比 diaries 少很多，从日记文件中提取缺失的分数
    scored_dates = {s.get("date") for s in scores}
    missing = [d for d in diaries if d["date"] not in scored_dates]
    if missing:
        import re as _re
        backfilled = 0
        for d in missing:
            try:
                content = _read_file_safe(ctx, f"{ctx.emotion_notes_dir}/{d['file']}")
                if not content:
                    continue
                # 匹配 **整体评分**：7/10 或 **整体评分**: 7/10
                m = _re.search(r'\*\*整体评分\*\*[：:]\s*(\d+)\s*/\s*10', content)
                if not m:
                    continue
                score = int(m.group(1))
                # 提取情绪标签
                lm = _re.search(r'\*\*情绪标签\*\*[：:]\s*(.+)', content)
                label = lm.group(1).strip() if lm else ""
                scores.append({
                    "date": d["date"],
                    "score": score,
                    "label": label,
                    "source": "backfill"
                })
                backfilled += 1
            except Exception as e:
                _log(f"[WebAPI] mood backfill error for {d['date']}: {e}")
        if backfilled:
            scores.sort(key=lambda s: s.get("date", ""))
            # 写回 state 持久化
            try:
                state["mood_scores"] = scores
                ctx.IO.write_json(ctx.state_file, state)
                _log(f"[WebAPI] mood_scores 回填 {backfilled} 条，已写回 state")
            except Exception as e:
                _log(f"[WebAPI] mood_scores 回填写入失败: {e}")

    return jsonify({"scores": scores, "diaries": diaries})


@api_bp.route("/memory", methods=["GET"])
@require_auth
def api_memory(user_id=None):
    """GET /api/memory — 获取长期记忆（结构化解析 memory.md）"""
    ctx = _get_ctx(user_id)

    content = _read_file_safe(ctx, ctx.memory_file)
    if not content:
        return jsonify({"sections": [], "total_items": 0, "last_updated": ""})

    # 按 ## 标题分段
    _SECTION_ICONS = {
        "用户画像": "👤", "重要的人": "👥", "偏好": "💡",
        "近期关注": "🔍", "重要事件": "📌", "工作": "💼",
        "习惯": "🔄", "健康": "🏥", "财务": "💰",
    }
    sections = []
    total_items = 0
    parts = re.split(r'\n(?=## )', content)
    for part in parts:
        part = part.strip()
        if not part.startswith("## "):
            continue
        lines = part.split("\n")
        title = lines[0].replace("## ", "").strip()
        items = [l.lstrip("- ").strip() for l in lines[1:] if l.strip().startswith("- ")]
        if not items:
            # 非列表段落也保留
            body = "\n".join(lines[1:]).strip()
            if body:
                items = [body]
        icon = _SECTION_ICONS.get(title, "📋")
        total_items += len(items)
        sections.append({"title": title, "icon": icon, "items": items})

    # 获取文件修改时间（仅 local 模式可获取精确时间）
    last_updated = ""
    try:
        if ctx.storage_mode == "local" and os.path.exists(ctx.memory_file):
            mtime = os.path.getmtime(ctx.memory_file)
            last_updated = datetime.fromtimestamp(mtime, _BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    return jsonify({"sections": sections, "total_items": total_items, "last_updated": last_updated})


@api_bp.route("/books", methods=["GET"])
@require_auth
def api_books(user_id=None):
    """GET /api/books — 获取读书笔记"""
    ctx = _get_ctx(user_id)

    books = []
    files = _list_files_safe(ctx, ctx.book_notes_dir, "*.md")
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
    ctx = _get_ctx(user_id)

    items = []
    files = _list_files_safe(ctx, ctx.media_notes_dir, "*.md")
    for f in sorted(files, reverse=True):
        items.append({
            "file": f,
            "title": f.replace(".md", ""),
        })

    return jsonify({"items": items})


# ============================================================
# 用户侧新增 API — 碎碎念 / 设置 / 决策 / 深度自问 / 微习惯
# ============================================================

@api_bp.route("/misc", methods=["GET"])
@require_auth
def api_misc(user_id=None):
    """GET /api/misc — 获取碎碎念"""
    ctx = _get_ctx(user_id)
    raw = _read_file_safe(ctx, ctx.misc_file)
    if not raw:
        return jsonify({"notes": [], "has_more": False, "total": 0})

    sections = raw.split("\n## ")[1:]
    notes = []
    for s in sections:
        lines = s.strip().split("\n")
        if not lines:
            continue
        header = lines[0].strip()
        content = "\n".join(lines[1:]).strip()
        if content.endswith("---"):
            content = content[:-3].strip()
        notes.append({"time": header, "content": content})

    notes.reverse()
    limit = int(request.args.get("limit", "50"))
    offset = int(request.args.get("offset", "0"))
    total = len(notes)
    page = notes[offset:offset + limit]

    return jsonify({"notes": page, "total": total, "has_more": (offset + limit) < total})


@api_bp.route("/settings", methods=["GET"])
@require_auth
def api_settings(user_id=None):
    """GET /api/settings — 获取用户设置（脱敏）"""
    ctx = _get_ctx(user_id)
    cfg = ctx.config
    safe_cfg = {
        "nickname": cfg.get("nickname", ""),
        "ai_name": cfg.get("ai_name", "Karvis"),
        "soul_override": cfg.get("soul_override", ""),
        "preferences": {
            "morning_report": cfg.get("preferences", {}).get("morning_report", True),
            "evening_checkin": cfg.get("preferences", {}).get("evening_checkin", True),
            "companion": cfg.get("preferences", {}).get("companion", True),
            "reflect": cfg.get("preferences", {}).get("reflect", True),
        },
        "skills_mode": cfg.get("skills", {}).get("mode", "blacklist"),
        "skills_list": cfg.get("skills", {}).get("list", []),
        "storage_mode": cfg.get("storage_mode", "local"),
        "role": cfg.get("role", ""),
    }
    return jsonify(safe_cfg)


@api_bp.route("/settings", methods=["POST"])
@require_auth
def api_update_settings(user_id=None):
    """POST /api/settings — 更新用户设置"""
    ctx = _get_ctx(user_id)
    data = request.get_json(force=True, silent=True) or {}

    WRITABLE = {"nickname", "ai_name", "soul_override"}
    WRITABLE_PREFS = {"morning_report", "evening_checkin", "companion", "reflect"}

    cfg = ctx.config
    changed = False

    for key in WRITABLE:
        if key in data and data[key] != cfg.get(key):
            cfg[key] = data[key]
            changed = True

    if "preferences" in data and isinstance(data["preferences"], dict):
        prefs = cfg.setdefault("preferences", {})
        for key in WRITABLE_PREFS:
            if key in data["preferences"] and data["preferences"][key] != prefs.get(key):
                prefs[key] = bool(data["preferences"][key])
                changed = True

    if changed:
        ctx.save_user_config(cfg)
        # 如果修改了昵称，同步更新注册表
        if "nickname" in data:
            try:
                from user_context import update_user_nickname
                update_user_nickname(user_id, cfg.get("nickname", ""))
            except Exception:
                pass
        _log(f"[WebAPI] 用户 {user_id} 更新设置: {list(data.keys())}")

    return jsonify({"ok": True, "changed": changed})


@api_bp.route("/decisions", methods=["GET"])
@require_auth
def api_decisions(user_id=None):
    """GET /api/decisions — 决策追踪"""
    ctx = _get_ctx(user_id)
    state = _read_state_safe(ctx)

    pending = state.get("pending_decisions", [])
    history = state.get("decision_history", [])
    pending.sort(key=lambda d: d.get("date", ""), reverse=True)
    history.sort(key=lambda d: d.get("review", {}).get("date", d.get("date", "")), reverse=True)

    satisfactions = [h["review"]["satisfaction"] for h in history
                     if h.get("review", {}).get("satisfaction")]
    avg_sat = round(sum(satisfactions) / len(satisfactions), 1) if satisfactions else None

    return jsonify({
        "pending": pending,
        "history": history,
        "stats": {
            "total": len(pending) + len(history),
            "pending_count": len(pending),
            "reviewed_count": len(history),
            "avg_satisfaction": avg_sat,
        }
    })


@api_bp.route("/reflect", methods=["GET"])
@require_auth
def api_reflect(user_id=None):
    """GET /api/reflect — 深度自问历史"""
    ctx = _get_ctx(user_id)
    state = _read_state_safe(ctx)

    # 读取 reflect_log.jsonl（始终本地存储）
    log_path = os.path.join(ctx.base_dir, "_Karvis", "reflect", "reflect_log.jsonl")
    entries = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass

    entries.sort(key=lambda e: e.get("date", ""), reverse=True)

    category = request.args.get("category")
    if category:
        entries = [e for e in entries if e.get("category") == category]

    limit = min(int(request.args.get("limit", 20)), 50)
    offset = int(request.args.get("offset", 0))
    total = len(entries)
    entries = entries[offset:offset + limit]

    stats = state.get("reflect_stats", {})

    return jsonify({
        "stats": {
            "total_answered": stats.get("total_answered", 0),
            "streak": stats.get("streak", 0),
            "category_counts": stats.get("category_counts", {}),
        },
        "entries": entries,
        "total": total,
        "categories": ["self", "relationship", "growth", "values", "future", "past"],
    })


@api_bp.route("/habits", methods=["GET"])
@require_auth
def api_habits(user_id=None):
    """GET /api/habits — 微习惯实验"""
    ctx = _get_ctx(user_id)
    state = _read_state_safe(ctx)

    active = state.get("active_experiment")
    history = state.get("experiment_history", [])

    completed = [h for h in history if h.get("completed")]
    rates = []
    for h in history:
        progress = h.get("progress", [])
        target = h.get("target_days", 1)
        done_days = sum(1 for p in progress if p.get("done"))
        rates.append(done_days / target if target > 0 else 0)

    return jsonify({
        "active": active,
        "history": history,
        "stats": {
            "total_experiments": len(history) + (1 if active else 0),
            "completed": len(completed),
            "avg_completion_rate": round(sum(rates) / len(rates) * 100) if rates else None,
        }
    })


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


@api_bp.route("/admin/stats", methods=["GET"])
@require_admin
def api_admin_stats():
    """GET /api/admin/stats — 延迟 + Token + 技能统计"""
    import glob
    from collections import defaultdict

    days = int(request.args.get("days", "14"))
    now = datetime.now(_BEIJING_TZ)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # --- 1. 读取 usage_log.jsonl (Token 用量) ---
    usage_entries = []
    try:
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("ts", "")[:10] >= cutoff:
                            usage_entries.append(e)
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass

    # Token 按日汇总
    token_daily = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
    token_by_model = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
    token_by_user = defaultdict(lambda: {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
    today_str = now.strftime("%Y-%m-%d")

    for e in usage_entries:
        day = e.get("ts", "")[:10]
        pt = e.get("prompt_tokens", 0)
        ct = e.get("completion_tokens", 0)
        tt = e.get("total_tokens", 0)
        model = e.get("model", "unknown")
        uid = e.get("user_id", "unknown")

        token_daily[day]["prompt"] += pt
        token_daily[day]["completion"] += ct
        token_daily[day]["total"] += tt
        token_daily[day]["calls"] += 1

        token_by_model[model]["prompt"] += pt
        token_by_model[model]["completion"] += ct
        token_by_model[model]["total"] += tt
        token_by_model[model]["calls"] += 1

        token_by_user[uid]["prompt"] += pt
        token_by_user[uid]["completion"] += ct
        token_by_user[uid]["total"] += tt
        token_by_user[uid]["calls"] += 1

    # 今日汇总
    today_stats = token_daily.get(today_str, {"prompt": 0, "completion": 0, "total": 0, "calls": 0})

    # 成本估算 (DeepSeek: 输入¥2/M 输出¥8/M, Qwen Flash: 免费, Qwen VL: 输入¥3/M 输出¥9/M)
    total_cost = 0.0
    for model, stats in token_by_model.items():
        m = model.lower()
        if "deepseek" in m:
            total_cost += stats["prompt"] / 1e6 * 2 + stats["completion"] / 1e6 * 8
        elif "vl" in m or "vl-max" in m:
            total_cost += stats["prompt"] / 1e6 * 3 + stats["completion"] / 1e6 * 9
        # qwen-flash 免费

    today_cost = 0.0
    for e in usage_entries:
        if e.get("ts", "")[:10] == today_str:
            m = e.get("model", "").lower()
            pt = e.get("prompt_tokens", 0)
            ct = e.get("completion_tokens", 0)
            if "deepseek" in m:
                today_cost += pt / 1e6 * 2 + ct / 1e6 * 8
            elif "vl" in m:
                today_cost += pt / 1e6 * 3 + ct / 1e6 * 9

    # --- 2. 读取各用户 decisions.jsonl (延迟 + 技能) ---
    decisions = []
    users_dir = os.path.join(DATA_DIR, "users")
    try:
        decision_files = glob.glob(os.path.join(users_dir, "*", "_Karvis", "logs", "decisions.jsonl"))
        for df in decision_files:
            uid = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(df))))
            with open(df, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("ts", "")[:10] >= cutoff:
                            e["user_id"] = uid
                            decisions.append(e)
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass

    # 按时间倒排，取最近 100 条用于延迟瀑布图
    decisions.sort(key=lambda x: x.get("ts", ""), reverse=True)
    recent_decisions = decisions[:100]

    # 延迟分布统计
    latencies = [d.get("elapsed_s", 0) for d in decisions if d.get("elapsed_s")]
    latency_stats = {}
    if latencies:
        latencies_sorted = sorted(latencies)
        latency_stats = {
            "avg": round(sum(latencies) / len(latencies), 1),
            "p50": round(latencies_sorted[len(latencies_sorted) // 2], 1),
            "p90": round(latencies_sorted[int(len(latencies_sorted) * 0.9)], 1),
            "p99": round(latencies_sorted[int(len(latencies_sorted) * 0.99)], 1),
            "max": round(max(latencies), 1),
            "count": len(latencies),
            "slow_15s": len([l for l in latencies if l > 15]),
            "slow_8s": len([l for l in latencies if l > 8]),
        }

    # 技能频次统计
    skill_counts = defaultdict(int)
    skill_by_user = defaultdict(lambda: defaultdict(int))
    for d in decisions:
        sk = d.get("skill", "unknown")
        uid = d.get("user_id", "unknown")
        skill_counts[sk] += 1
        skill_by_user[uid][sk] += 1

    skill_top = sorted(skill_counts.items(), key=lambda x: -x[1])[:15]

    # --- 3. 本月成本（用于预算预警） ---
    month_str = now.strftime("%Y-%m")
    month_cost = 0.0
    for e in usage_entries:
        if e.get("ts", "")[:7] == month_str:
            m = e.get("model", "").lower()
            pt = e.get("prompt_tokens", 0)
            ct = e.get("completion_tokens", 0)
            if "deepseek" in m:
                month_cost += pt / 1e6 * 2 + ct / 1e6 * 8
            elif "vl" in m:
                month_cost += pt / 1e6 * 3 + ct / 1e6 * 9

    # --- 4. Prompt Token 分布（膨胀检测） ---
    prompt_dist = {"lt4k": 0, "4k_8k": 0, "8k_12k": 0, "gt12k": 0}
    for e in usage_entries:
        pt = e.get("prompt_tokens", 0)
        if pt < 4000:
            prompt_dist["lt4k"] += 1
        elif pt < 8000:
            prompt_dist["4k_8k"] += 1
        elif pt < 12000:
            prompt_dist["8k_12k"] += 1
        else:
            prompt_dist["gt12k"] += 1

    # --- 5. 错误日志聚合 ---
    error_groups = _aggregate_error_logs()

    return jsonify({
        "token": {
            "daily": dict(token_daily),
            "by_model": dict(token_by_model),
            "by_user": dict(token_by_user),
            "today": today_stats,
            "total_cost": round(total_cost, 2),
            "today_cost": round(today_cost, 4),
            "month_cost": round(month_cost, 2),
            "prompt_dist": prompt_dist,
        },
        "latency": {
            "recent": [{
                "ts": d.get("ts", ""),
                "user_id": d.get("user_id", ""),
                "skill": d.get("skill", ""),
                "elapsed_s": d.get("elapsed_s", 0),
                "input_type": d.get("input_type", ""),
                "action": d.get("action", ""),
                "has_reply": d.get("has_reply", False),
            } for d in recent_decisions],
            "stats": latency_stats,
        },
        "skills": {
            "top": skill_top,
            "by_user": {uid: dict(sk) for uid, sk in skill_by_user.items()},
        },
        "errors": error_groups,
        "period_days": days,
    })


def _aggregate_error_logs():
    """从日志文件中提取 ERROR/Traceback，去重聚合计数"""
    from collections import deque
    error_groups = []
    try:
        if not os.path.exists(LOG_FILE_KARVISFORALL):
            return []
        with open(LOG_FILE_KARVISFORALL, "r", encoding="utf-8", errors="replace") as f:
            lines = list(deque(f, maxlen=5000))  # 最近 5000 行

        seen = {}  # {error_key: {count, last_ts, sample}}
        i = 0
        while i < len(lines):
            line = lines[i].rstrip("\n")
            is_error = "[ERROR]" in line.upper() or "Traceback" in line
            if is_error:
                # 收集多行 traceback
                block = [line]
                if "Traceback" in line:
                    j = i + 1
                    while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")
                                               or "Error" in lines[j] or "Exception" in lines[j]):
                        block.append(lines[j].rstrip("\n"))
                        j += 1
                    i = j
                else:
                    i += 1

                # 提取错误签名（最后一行的错误类型）
                last_line = block[-1].strip() if block else line
                # 取错误类型作为 key（如 "KeyError: 'xxx'" → "KeyError"）
                key = last_line.split(":")[0].strip() if ":" in last_line else last_line[:80]
                # 去掉时间戳前缀
                for prefix in ("[ERROR]", "[WARNING]"):
                    if prefix in key:
                        key = key[key.index(prefix):]

                if key not in seen:
                    seen[key] = {"count": 0, "last_ts": "", "sample": "\n".join(block[:10])}
                seen[key]["count"] += 1
                # 尝试提取时间戳
                ts_match = re.search(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', line)
                if ts_match:
                    seen[key]["last_ts"] = ts_match.group()
            else:
                i += 1

        error_groups = sorted(
            [{"key": k, **v} for k, v in seen.items()],
            key=lambda x: -x["count"]
        )[:20]  # Top 20 错误
    except Exception as e:
        _log(f"[WebAPI] 错误聚合失败: {e}")
    return error_groups


@api_bp.route("/admin/users/<uid>/skills", methods=["GET"])
@require_admin
def api_admin_user_skills(uid):
    """GET /api/admin/users/{uid}/skills — 查看用户 Skill 配置"""
    from skill_loader import get_skill_metadata

    ctx = _get_ctx(uid)
    config = ctx.get_user_config()
    skills_cfg = config.get("skills", {"mode": "blacklist", "list": []})

    # 所有已注册 Skill 的元数据
    all_meta = get_skill_metadata()
    skill_list = []
    for name, meta in sorted(all_meta.items()):
        vis = meta.get("visibility", "public")
        allowed = ctx.is_skill_allowed(name)
        skill_list.append({
            "name": name,
            "visibility": vis,
            "allowed": allowed,
            "description": meta.get("description", ""),
        })

    return jsonify({
        "user_id": uid,
        "mode": skills_cfg.get("mode", "blacklist"),
        "list": skills_cfg.get("list", []),
        "skills": skill_list,
    })


@api_bp.route("/admin/users/<uid>/skills", methods=["POST"])
@require_admin
def api_admin_update_user_skills(uid):
    """POST /api/admin/users/{uid}/skills — 更新用户 Skill 配置"""
    ctx = _get_ctx(uid)
    data = request.get_json(force=True, silent=True) or {}

    mode = data.get("mode")
    skill_list = data.get("list")

    if mode not in ("blacklist", "whitelist"):
        return jsonify({"error": "mode 必须为 blacklist 或 whitelist"}), 400
    if not isinstance(skill_list, list):
        return jsonify({"error": "list 必须为数组"}), 400

    config = ctx.get_user_config()
    skills_cfg = {"mode": mode, "list": skill_list}
    config["skills"] = skills_cfg
    ctx.save_user_config(config)
    ctx._skills_config = skills_cfg  # 同步内存缓存

    _log(f"[WebAPI] 更新用户 Skill 配置: uid={uid}, mode={mode}, list={skill_list}")
    return jsonify({"ok": True, "user_id": uid, "skills": config["skills"]})


@api_bp.route("/admin/users/<uid>/detail", methods=["GET"])
@require_admin
def api_admin_user_detail(uid):
    """GET /api/admin/users/{uid}/detail — 用户运营详情（不含隐私内容）"""
    ctx = _get_ctx(uid)
    config = ctx.get_user_config()
    result = {"user_id": uid}

    # 1. 基本配置
    result["config"] = {
        "nickname": config.get("nickname", ""),
        "ai_name": config.get("ai_name", "Karvis"),
        "role": config.get("role", "user"),
        "storage_mode": config.get("storage_mode", "local"),
        "onboarding_step": config.get("onboarding_step", 0),
        "preferences": config.get("preferences", {}),
        "skills": config.get("skills", {"mode": "blacklist", "list": []}),
        "daily_message_limit": config.get("daily_message_limit", 50),
    }

    # 2. 注册表信息
    users = get_all_users()
    user_reg = users.get(uid, {})
    result["registry"] = user_reg

    # 3. 最近决策日志（最新 20 条）
    decisions = []
    try:
        if os.path.exists(ctx.decision_log_file):
            from collections import deque
            with open(ctx.decision_log_file, "r", encoding="utf-8") as f:
                recent = list(deque(f, maxlen=20))
            for line in recent:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        decisions.append({
                            "ts": d.get("ts", ""),
                            "input_type": d.get("input_type", ""),
                            "input": (d.get("input", "") or "")[:100],
                            "skill": d.get("skill", ""),
                            "action": d.get("action", ""),
                            "elapsed_s": d.get("elapsed_s", 0),
                            "has_reply": d.get("has_reply", False),
                            "thinking": (d.get("thinking", "") or "")[:200],
                        })
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        _log(f"[WebAPI] 读取决策日志失败 {uid}: {e}")
    result["decisions"] = list(reversed(decisions))

    # 4. 存储用量
    storage = {"file_count": 0, "total_size_bytes": 0}
    try:
        if ctx.storage_mode == "local" and os.path.exists(ctx.base_dir):
            for root, dirs, files in os.walk(ctx.base_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    storage["file_count"] += 1
                    try:
                        storage["total_size_bytes"] += os.path.getsize(fpath)
                    except OSError:
                        pass
            storage["total_size_mb"] = round(storage["total_size_bytes"] / (1024 * 1024), 2)
    except Exception as e:
        _log(f"[WebAPI] 计算存储用量失败 {uid}: {e}")
    result["storage"] = storage

    # 5. State 关键字段（运营诊断用）
    state = _read_state_safe(ctx)
    result["state_summary"] = {
        "mood_scores_count": len(state.get("mood_scores", [])),
        "mood_latest": state.get("mood_scores", [{}])[-1] if state.get("mood_scores") else None,
        "nudge_streak": state.get("nudge_state", {}).get("streak", 0),
        "checkin_pending": state.get("checkin_pending", False),
        "last_daily_report": state.get("last_daily_report", ""),
        "last_weekly_review": state.get("last_weekly_review", ""),
        "last_monthly_review": state.get("last_monthly_review", ""),
        "daily_top3": state.get("daily_top3", {}),
    }

    # 6. Token 消耗（该用户）
    token_usage = {"total_tokens": 0, "calls": 0}
    try:
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("user_id") == uid:
                            token_usage["total_tokens"] += e.get("total_tokens", 0)
                            token_usage["calls"] += 1
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass
    result["token_usage"] = token_usage

    return jsonify(result)


@api_bp.route("/admin/users/<uid>/token", methods=["POST"])
@require_admin
def api_admin_generate_token(uid):
    """POST /api/admin/users/{uid}/token — 为用户生成 Web 访问令牌"""
    from user_context import generate_token
    data = request.get_json(force=True, silent=True) or {}
    expire_hours = data.get("expire_hours", 24)

    users = get_all_users()
    if uid not in users:
        return jsonify({"error": f"用户 {uid} 不存在"}), 404

    token = generate_token(uid, expire_hours=expire_hours)
    web_url = f"{request.host_url}web/login?token={token}"

    _log(f"[WebAPI] 管理员为用户 {uid} 生成令牌, expire_hours={expire_hours}")
    return jsonify({"ok": True, "user_id": uid, "token": token, "web_url": web_url, "expire_hours": expire_hours})


@api_bp.route("/admin/users/<uid>/config", methods=["POST"])
@require_admin
def api_admin_update_user_config(uid):
    """POST /api/admin/users/{uid}/config — 更新用户配置（消息限额、偏好等）"""
    ctx = _get_ctx(uid)
    data = request.get_json(force=True, silent=True) or {}
    config = ctx.get_user_config()

    # 可更新字段白名单
    if "daily_message_limit" in data:
        config["daily_message_limit"] = int(data["daily_message_limit"])
    if "preferences" in data and isinstance(data["preferences"], dict):
        prefs = config.get("preferences", {})
        prefs.update(data["preferences"])
        config["preferences"] = prefs
    if "onboarding_step" in data:
        config["onboarding_step"] = int(data["onboarding_step"])

    ctx.save_user_config(config)
    _log(f"[WebAPI] 管理员更新用户配置: uid={uid}, keys={list(data.keys())}")
    return jsonify({"ok": True, "user_id": uid, "config": config})


@api_bp.route("/admin/system/config", methods=["GET"])
@require_admin
def api_admin_system_config():
    """GET /api/admin/system/config — 系统配置总览（模型路由、运行参数等）"""
    from config import (
        DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
        CLAUDE_API_KEY, CLAUDE_BASE_URL, CLAUDE_MODEL,
        QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL, QWEN_VL_MODEL,
        ADMIN_USER_ID, SERVER_PORT, ACTIVE_CHANNELS,
        SCHEDULER_TICK_MINUTES, SCHEDULER_DEFAULT_WAKE, SCHEDULER_DEFAULT_SLEEP,
        SCHEDULER_PUSH_MAX_DAILY, SCHEDULER_MIN_PUSH_GAP,
        COMPANION_SILENT_HOURS, COMPANION_INTERVAL_HOURS, COMPANION_MAX_DAILY,
        ALERT_SLOW_THRESHOLD, ALERT_SLOW_CONSECUTIVE, ALERT_COOLDOWN_SECONDS,
        TELEGRAM_BOT_TOKEN, TELEGRAM_API_BASE,
    )
    from user_context import DAILY_MESSAGE_LIMIT, INACTIVE_DAYS_THRESHOLD

    def _mask(key):
        """脱敏 API Key，只显示前8位和后4位"""
        if not key:
            return "❌ 未配置"
        if len(key) <= 16:
            return key[:4] + "****"
        return key[:8] + "****" + key[-4:]

    config = {
        "models": {
            "main": {
                "label": "Main / Think",
                "provider": "DeepSeek",
                "model": DEEPSEEK_MODEL,
                "base_url": DEEPSEEK_BASE_URL,
                "api_key": _mask(DEEPSEEK_API_KEY),
                "status": "✅" if DEEPSEEK_API_KEY else "❌",
            },
            "flash": {
                "label": "Flash",
                "provider": "Qwen (阿里百炼)",
                "model": QWEN_MODEL,
                "base_url": QWEN_BASE_URL,
                "api_key": _mask(QWEN_API_KEY),
                "status": "✅" if QWEN_API_KEY else "❌",
            },
            "vl": {
                "label": "视觉理解 (VL)",
                "provider": "Qwen VL",
                "model": QWEN_VL_MODEL,
                "base_url": QWEN_BASE_URL,
                "api_key": _mask(QWEN_API_KEY),
                "status": "✅" if QWEN_API_KEY else "❌",
            },
            "claude": {
                "label": "管理员专属",
                "provider": "Anthropic Claude",
                "model": CLAUDE_MODEL,
                "base_url": CLAUDE_BASE_URL,
                "api_key": _mask(CLAUDE_API_KEY),
                "status": "✅" if CLAUDE_API_KEY else "❌ 未配置（管理员走 DeepSeek）",
            },
        },
        "routing": {
            "admin_model": "Claude" if CLAUDE_API_KEY else "DeepSeek (Claude 未配置)",
            "user_model": "DeepSeek",
            "flash_model": "Qwen Flash",
            "skill_internal": "DeepSeek (不走 Claude，省成本)",
        },
        "scheduler": {
            "tick_minutes": SCHEDULER_TICK_MINUTES,
            "default_wake": SCHEDULER_DEFAULT_WAKE,
            "default_sleep": SCHEDULER_DEFAULT_SLEEP,
            "push_max_daily": SCHEDULER_PUSH_MAX_DAILY,
            "min_push_gap": f"{SCHEDULER_MIN_PUSH_GAP} 分钟",
        },
        "companion": {
            "silent_hours": COMPANION_SILENT_HOURS,
            "interval_hours": COMPANION_INTERVAL_HOURS,
            "max_daily": COMPANION_MAX_DAILY,
        },
        "alerts": {
            "slow_threshold": f"{ALERT_SLOW_THRESHOLD}s",
            "slow_consecutive": ALERT_SLOW_CONSECUTIVE,
            "cooldown": f"{ALERT_COOLDOWN_SECONDS}s",
        },
        "channels": {
            "active": ACTIVE_CHANNELS,
            "telegram": "✅" if TELEGRAM_BOT_TOKEN else "❌ 未配置",
            "telegram_api_base": TELEGRAM_API_BASE,
        },
        "system": {
            "server_port": SERVER_PORT,
            "admin_user_id": ADMIN_USER_ID or "未配置",
            "daily_message_limit": DAILY_MESSAGE_LIMIT,
            "inactive_days_threshold": INACTIVE_DAYS_THRESHOLD,
        },
    }
    return jsonify(config)


@api_bp.route("/admin/system/action", methods=["POST"])
@require_admin
def api_admin_system_action():
    """POST /api/admin/system/action — 手动触发系统动作"""
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "")
    target_user = data.get("user_id", "")

    valid_actions = [
        "refresh_cache", "daily_init", "scheduler_tick",
        "morning_report", "evening_checkin", "daily_report",
        "todo_remind", "reflect_push", "mood_generate",
        "weekly_review", "monthly_review", "nudge_check", "companion_check",
    ]
    if action not in valid_actions:
        return jsonify({"error": f"无效的 action: {action}", "valid_actions": valid_actions}), 400

    # 转发到内部 /system 端点
    import requests as _requests
    try:
        payload = {"action": action}
        if target_user:
            payload["user_id"] = target_user
        resp = _requests.post("http://127.0.0.1:9000/system", json=payload, timeout=60)
        result = resp.json()
        _log(f"[WebAPI] 管理员触发系统动作: action={action}, user={target_user or 'all'}, result_ok={result.get('ok')}")
        return jsonify(result)
    except Exception as e:
        _log(f"[WebAPI] 系统动作执行失败: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/admin/logs", methods=["GET"])
@require_admin
def api_admin_logs():
    """GET /api/admin/logs — 查看服务日志（V12 精简版）
    参数: lines(最大行数), keyword(关键词), level(日志级别), user(用户ID)
    """
    from collections import deque

    lines = min(int(request.args.get("lines", "200")), 2000)
    keyword = request.args.get("keyword", "").strip()
    level = request.args.get("level", "").upper()
    user_filter = request.args.get("user", "").strip()

    log_lines = []

    # 读取 V12 服务日志
    try:
        if os.path.exists(LOG_FILE_KARVISFORALL):
            with open(LOG_FILE_KARVISFORALL, "r", encoding="utf-8", errors="replace") as f:
                log_lines = list(deque(f, maxlen=lines))
            log_lines = [l.rstrip("\n") for l in log_lines]
        else:
            log_lines = [f"[WARN] 日志文件不存在: {LOG_FILE_KARVISFORALL}"]
    except Exception as e:
        log_lines = [f"[ERROR] 读取日志失败: {e}"]

    # 用户过滤
    if user_filter:
        log_lines = [l for l in log_lines if user_filter.lower() in l.lower()]

    # 关键词过滤
    if keyword:
        kw_lower = keyword.lower()
        log_lines = [l for l in log_lines if kw_lower in l.lower()]

    # 级别过滤
    if level and level != "ALL":
        log_lines = [l for l in log_lines if level in l.upper()]

    return jsonify({"lines": log_lines, "total": len(log_lines), "project": "karvisforall"})


# ============================================================
# 邀请码 API — /api/admin/invite-codes/*
# ============================================================

@api_bp.route("/admin/invite-codes", methods=["GET"])
@require_admin
def api_admin_invite_codes_list():
    """GET /api/admin/invite-codes — 获取所有邀请码"""
    codes = get_all_invite_codes()
    return jsonify({"codes": codes})


@api_bp.route("/admin/invite-codes", methods=["POST"])
@require_admin
def api_admin_invite_codes_create():
    """POST /api/admin/invite-codes — 生成邀请码"""
    code = create_invite_code("admin")
    _log(f"[WebAPI] 生成邀请码: {code}")
    return jsonify({"ok": True, "code": code})


@api_bp.route("/admin/invite-codes/<code>", methods=["DELETE"])
@require_admin
def api_admin_invite_codes_delete(code):
    """DELETE /api/admin/invite-codes/{code} — 删除邀请码"""
    ok = delete_invite_code(code)
    if not ok:
        return jsonify({"error": "邀请码不存在"}), 404
    return jsonify({"ok": True})


# ============================================================
# 公告 API — /api/admin/announcements/*
# ============================================================

@api_bp.route("/admin/announcements", methods=["GET"])
@require_admin
def api_admin_announcements_list():
    """GET /api/admin/announcements — 获取所有公告"""
    return jsonify({"announcements": get_announcements()})


@api_bp.route("/admin/announcements", methods=["POST"])
@require_admin
def api_admin_announcements_create():
    """POST /api/admin/announcements — 发布公告"""
    data = request.get_json(force=True, silent=True) or {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    ann = create_announcement(title, content)
    _log(f"[WebAPI] 发布公告: {title}")
    return jsonify({"ok": True, "announcement": ann})


@api_bp.route("/admin/announcements/<ann_id>", methods=["DELETE"])
@require_admin
def api_admin_announcements_delete(ann_id):
    """DELETE /api/admin/announcements/{id} — 删除公告"""
    ok = delete_announcement(ann_id)
    if not ok:
        return jsonify({"error": "公告不存在"}), 404
    return jsonify({"ok": True})


# ============================================================
# 用户反馈 API — /api/admin/feedbacks/*
# ============================================================

@api_bp.route("/admin/feedbacks", methods=["GET"])
@require_admin
def api_admin_feedbacks_list():
    """GET /api/admin/feedbacks — 获取所有用户反馈"""
    return jsonify({"feedbacks": get_feedbacks()})


@api_bp.route("/admin/feedbacks/<fb_id>/reply", methods=["POST"])
@require_admin
def api_admin_feedbacks_reply(fb_id):
    """POST /api/admin/feedbacks/{id}/reply — 回复用户反馈"""
    data = request.get_json(force=True, silent=True) or {}
    reply_text = data.get("reply", "").strip()
    if not reply_text:
        return jsonify({"error": "回复内容不能为空"}), 400
    ok = reply_feedback(fb_id, reply_text)
    if not ok:
        return jsonify({"error": "反馈不存在"}), 404
    _log(f"[WebAPI] 回复反馈: {fb_id}")
    return jsonify({"ok": True})


# ============================================================
# 用户侧公告 + 反馈提交 API（无需管理员权限，用户 token 鉴权）
# ============================================================

@api_bp.route("/announcements", methods=["GET"])
@require_auth
def api_user_announcements(user_id=None):
    """GET /api/announcements — 用户获取公告列表"""
    anns = get_announcements()
    return jsonify({"announcements": anns[:10]})  # 最近 10 条


@api_bp.route("/feedback", methods=["POST"])
@require_auth
def api_user_feedback(user_id=None):
    """POST /api/feedback — 用户提交反馈"""
    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "反馈内容不能为空"}), 400
    fb = create_feedback(user_id, content)
    _log(f"[WebAPI] 用户 {user_id} 提交反馈: {content[:50]}")
    return jsonify({"ok": True, "feedback": fb})


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


@web_bp.route("/memory")
def web_memory():
    return _serve_page("memory.html")


@web_bp.route("/settings")
def web_settings():
    return _serve_page("settings.html")


@web_bp.route("/decisions")
def web_decisions():
    return _serve_page("decisions.html")


@web_bp.route("/reflect")
def web_reflect():
    return _serve_page("reflect.html")


@web_bp.route("/habits")
def web_habits():
    return _serve_page("habits.html")


@web_bp.route("/admin")
def web_admin():
    return _serve_page("admin.html")


@web_bp.route("/logs")
def web_logs():
    return _serve_page("logs.html")


def _serve_page(filename):
    """提供静态 HTML 页面"""
    if not os.path.exists(os.path.join(_STATIC_DIR, filename)):
        return f"页面 {filename} 不存在", 404
    return send_from_directory(_STATIC_DIR, filename)


@web_bp.route("/static/<path:filename>")
def web_static_file(filename):
    """提供静态资源文件（JS/CSS 等）"""
    return send_from_directory(_STATIC_DIR, filename)


# ============================================================
# 工具函数（V12: 统一走 ctx.IO，兼容 Local/OneDrive）
# ============================================================

def _join_path(ctx, base_dir, filename):
    """拼接路径：本地用 os.path.join，OneDrive 用 / 拼接"""
    if ctx.storage_mode == "local":
        return os.path.join(base_dir, filename)
    return f"{base_dir}/{filename}"


def _read_file_safe(ctx, filepath):
    """安全读取文件，通过 ctx.IO 抽象层，失败返回空字符串"""
    try:
        content = ctx.IO.read_text(filepath)
        return content if content is not None else ""
    except Exception as e:
        _log(f"[WebAPI] 读取文件失败 {filepath}: {e}")
    return ""


def _read_state_safe(ctx):
    """安全读取用户 state（JSON）"""
    try:
        data = ctx.IO.read_json(ctx.state_file)
        return data if data is not None else {}
    except Exception as e:
        _log(f"[WebAPI] 读取 state 失败: {e}")
    return {}


def _list_files_safe(ctx, dir_path, pattern="*"):
    """安全列出目录下的文件，通过 ctx.IO.list_children 抽象层。
    返回文件名列表（不含路径），支持 fnmatch pattern 过滤。
    """
    import fnmatch as _fnmatch
    try:
        children = ctx.IO.list_children(dir_path)
        if children is None:
            return []
        # list_children 返回 [{"name": ..., "file": {...}}, ...]
        # 只取文件（有 "file" key 的），排除文件夹
        names = [c["name"] for c in children if "file" in c]
        if pattern and pattern != "*":
            names = [n for n in names if _fnmatch.fnmatch(n, pattern)]
        return names
    except Exception as e:
        _log(f"[WebAPI] 列出文件失败 {dir_path}: {e}")
    return []


def _read_first_line(ctx, filepath):
    """读取文件第一行（去除 # 标记），用于获取标题"""
    try:
        content = ctx.IO.read_text(filepath)
        if content:
            line = content.split("\n", 1)[0].strip()
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
