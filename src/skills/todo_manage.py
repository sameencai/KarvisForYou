# -*- coding: utf-8 -*-
"""
Skill: todo.*
待办清单管理：添加、完成、查看。
数据存储在 Obsidian 的 Todo.md 中。
"""
import sys
import re
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _now_str():
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def _parse_todo_md(text):
    """
    解析 Todo.md，返回 (doing_items, done_items)。
    每个 item 是 {"raw": "原始行", "content": "纯文本", "date": "日期标签"}
    """
    doing = []
    done = []
    current_section = None

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## 进行中"):
            current_section = "doing"
            continue
        elif stripped.startswith("## 已完成"):
            current_section = "done"
            continue
        elif stripped.startswith("## "):
            current_section = None
            continue

        if not stripped.startswith("- ["):
            continue

        # 提取内容和日期
        content = stripped
        date_tag = ""
        date_match = re.search(r'`(\d{4}-\d{2}-\d{2})`', stripped)
        if date_match:
            date_tag = date_match.group(1)
            content = stripped.replace(date_match.group(0), "").strip()

        # 去掉 checkbox 标记
        content = re.sub(r'^- \[[ x]\] ', '', content).strip()

        item = {"raw": stripped, "content": content, "date": date_tag}

        if current_section == "doing":
            doing.append(item)
        elif current_section == "done":
            done.append(item)

    return doing, done


def _rebuild_todo_md(doing_items, done_items):
    """重建 Todo.md 文件内容"""
    lines = ["# 📋 待办清单", ""]
    lines.append("## 进行中")
    for item in doing_items:
        lines.append(item["raw"])
    lines.append("")
    lines.append("## 已完成")
    for item in done_items:
        lines.append(item["raw"])
    lines.append("")
    return "\n".join(lines)


# ============ Skill 入口 ============


def add(params, state, ctx):
    """
    添加待办事项。

    params:
        content: str — 待办内容
        due_date: str — 可选，截止日期 YYYY-MM-DD
        remind_at: str — 可选，提醒时间 YYYY-MM-DD HH:MM
    """
    content = (params.get("content") or "").strip()
    if not content:
        return {"success": False, "reply": "待办内容不能为空"}

    due_date = (params.get("due_date") or "").strip()
    remind_at = (params.get("remind_at") or "").strip()

    # 读取现有文件
    text = OneDriveIO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    if not text.strip():
        text = "# 📋 待办清单\n\n## 进行中\n\n## 已完成\n"

    doing, done = _parse_todo_md(text)

    # 构建新条目
    date_tag = f" `{_now_str()}`"
    due_tag = f" 📅 {due_date}" if due_date else ""
    remind_tag = f" ⏰ {remind_at}" if remind_at else ""
    new_line = f"- [ ] {content}{due_tag}{remind_tag}{date_tag}"
    doing.append({"raw": new_line, "content": content, "date": _now_str()})

    # 注册提醒到 state
    state_updates = {}
    if remind_at or due_date:
        reminders = state.get("reminders", [])
        reminder = {"content": content, "created": _now_str()}
        if remind_at:
            reminder["remind_at"] = remind_at
            reminder["type"] = "timed"
        if due_date:
            reminder["due_date"] = due_date
            reminder["type"] = reminder.get("type", "deadline")
        reminders.append(reminder)
        state_updates["reminders"] = reminders

    # 写回文件
    new_text = _rebuild_todo_md(doing, done)
    ok = OneDriveIO.write_text(ctx.todo_file, new_text)

    if ok:
        _log(f"[todo.add] 已添加: {content}")
        result = {"success": True}
        if state_updates:
            result["state_updates"] = state_updates
        return result
    else:
        return {"success": False, "reply": "写入 Todo.md 失败"}


def complete(params, state, ctx):
    """
    完成待办事项，支持关键词匹配和序号批量完成。

    params:
        keyword: str — 用于匹配待办的关键词
        indices: str — 用序号完成，支持 "3" / "2-7" / "1,3,5"
    """
    keyword = (params.get("keyword") or "").strip().lower()
    indices_str = (params.get("indices") or "").strip()

    if not keyword and not indices_str:
        return {"success": False, "reply": "请告诉我要完成哪个待办"}

    text = OneDriveIO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)

    if indices_str:
        # ── 序号模式：批量完成 ──
        target_indices = _parse_indices(indices_str, len(doing))
        if not target_indices:
            return {"success": False, "reply": f"无法解析序号「{indices_str}」，或序号超出范围"}

        completed = []
        for idx in sorted(target_indices, reverse=True):
            if 0 <= idx < len(doing):
                item = doing.pop(idx)
                done_line = item["raw"].replace("- [ ]", "- [x]")
                if f"`{_now_str()}`" not in done_line:
                    done_line += f" ✅ `{_now_str()}`"
                done.insert(0, {"raw": done_line, "content": item["content"], "date": _now_str()})
                completed.append(item["content"])

        if not completed:
            return {"success": False, "reply": "没有找到对应序号的待办"}

        state_updates = _clean_reminders(state, completed)
        new_text = _rebuild_todo_md(doing, done)
        ok = OneDriveIO.write_text(ctx.todo_file, new_text)

        if ok:
            names = "、".join(f"「{c[:20]}」" for c in completed)
            _log(f"[todo.done] 批量完成 {len(completed)} 条: {names}")
            result = {"success": True, "reply": f"已完成 {len(completed)} 条待办 ✅\n{names}"}
            if state_updates:
                result["state_updates"] = state_updates
            return result
        return {"success": False, "reply": "写入 Todo.md 失败"}

    else:
        # ── 关键词模式：单条匹配 ──
        matched = None
        matched_idx = -1
        for i, item in enumerate(doing):
            if keyword in item["content"].lower():
                matched = item
                matched_idx = i
                break

        if not matched:
            return {"success": False, "reply": f"没找到包含「{keyword}」的待办"}

        doing.pop(matched_idx)
        done_line = matched["raw"].replace("- [ ]", "- [x]")
        if f"`{_now_str()}`" not in done_line:
            done_line += f" ✅ `{_now_str()}`"
        done.insert(0, {"raw": done_line, "content": matched["content"], "date": _now_str()})

        state_updates = _clean_reminders(state, [matched["content"]])
        new_text = _rebuild_todo_md(doing, done)
        ok = OneDriveIO.write_text(ctx.todo_file, new_text)

        if ok:
            _log(f"[todo.done] 已完成: {matched['content']}")
            result = {"success": True, "reply": f"已完成「{matched['content']}」✅"}
            if state_updates:
                result["state_updates"] = state_updates
            return result
        return {"success": False, "reply": "写入 Todo.md 失败"}


def _parse_indices(s, max_len):
    """
    解析序号字符串，返回 0-based 索引列表。
    支持: "3" / "2-7" / "1,3,5" / "2、4、6" / "2到7" / "2~7"
    """
    s = s.replace("、", ",").replace("到", "-").replace("~", "-").replace("～", "-")
    # 去掉中文前缀
    s = re.sub(r'^第', '', s)
    s = re.sub(r'个$', '', s)
    indices = set()
    for part in re.split(r'[,\s]+', s):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a.strip()), int(b.strip())
                for i in range(a, b + 1):
                    if 1 <= i <= max_len:
                        indices.add(i - 1)
            except ValueError:
                continue
        else:
            try:
                n = int(part)
                if 1 <= n <= max_len:
                    indices.add(n - 1)
            except ValueError:
                continue
    return sorted(indices)


def _clean_reminders(state, completed_contents):
    """清理已完成待办对应的 reminders"""
    reminders = state.get("reminders", [])
    new_reminders = reminders[:]
    for content in completed_contents:
        kw = content.lower()
        new_reminders = [r for r in new_reminders if kw not in r.get("content", "").lower()]
    if len(new_reminders) != len(reminders):
        return {"reminders": new_reminders}
    return {}


def list_todos(params, state, ctx):
    """
    查看待办清单（带序号，用户可用序号引用）。
    """
    text = OneDriveIO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)

    parts = []
    if doing:
        parts.append("📋 进行中:")
        for i, item in enumerate(doing, 1):
            parts.append(f"  {i}. {item['content']}")
    else:
        parts.append("📋 没有进行中的待办")

    if done:
        recent_done = done[:3]
        parts.append(f"\n✅ 最近完成 ({len(done)} 条):")
        for item in recent_done:
            parts.append(f"  · {item['content']}")

    return {"success": True, "reply": "\n".join(parts)}


def check_reminders(state):
    """
    检查到期提醒，返回需要推送的消息列表。
    由 /system?action=todo_remind 直接调用，不经过 LLM。

    检查逻辑：
    - remind_at 类型：提前 30 分钟预警 + 到时提醒
    - deadline 类型：当天提醒
    - 已推送的提醒打上 notified 标记，避免重复

    返回: {"messages": [str], "state_updates": dict}
    """
    reminders = state.get("reminders", [])
    if not reminders:
        return {"messages": [], "state_updates": {}}

    now = datetime.now(BEIJING_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    today_str = now.strftime("%Y-%m-%d")
    messages = []
    changed = False

    for r in reminders:
        if r.get("notified"):
            continue

        content = r.get("content", "")
        remind_at = r.get("remind_at", "")
        due_date = r.get("due_date", "")

        # 定时提醒：到时间就推
        if remind_at:
            try:
                remind_time = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
                remind_time = remind_time.replace(tzinfo=BEIJING_TZ)
                diff_minutes = (remind_time - now).total_seconds() / 60

                if diff_minutes <= 0:
                    # 已到时间
                    messages.append(f"⏰ 提醒：{content}")
                    r["notified"] = now_str
                    changed = True
                elif diff_minutes <= 30:
                    # 30 分钟内预警（只预警一次）
                    if not r.get("pre_notified"):
                        messages.append(f"⏰ {int(diff_minutes)} 分钟后：{content}")
                        r["pre_notified"] = now_str
                        changed = True
            except ValueError:
                pass

        # 截止日期提醒：当天提醒
        elif due_date:
            if due_date == today_str and not r.get("day_notified"):
                messages.append(f"📅 今天截止：{content}")
                r["day_notified"] = now_str
                changed = True
            elif due_date < today_str and not r.get("notified"):
                messages.append(f"⚠️ 已过期：{content}（截止 {due_date}）")
                r["notified"] = now_str
                changed = True

    # 清理已完成且已通知的过期提醒（超过 7 天的）
    cleaned = []
    for r in reminders:
        due = r.get("due_date", "")
        if r.get("notified") and due and due < today_str:
            try:
                due_dt = datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
                if (now - due_dt).days > 7:
                    continue  # 过期超过 7 天，移除
            except ValueError:
                pass
        cleaned.append(r)

    state_updates = {}
    if changed or len(cleaned) != len(reminders):
        state_updates["reminders"] = cleaned

    _log(f"[todo.remind] 检查 {len(reminders)} 条提醒, 推送 {len(messages)} 条")
    return {"messages": messages, "state_updates": state_updates}


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "todo.add": add,
    "todo.done": complete,
    "todo.list": list_todos,
}
