# -*- coding: utf-8 -*-
"""
Skill: todo.*
统一待办系统：一切都是待办，提醒/循环只是待办的属性。
数据源：state["todos"]（单一数据源），Todo.md 作为 Obsidian 展示层同步生成。

待办模型：
  content  — 内容
  due_date — 截止日期 YYYY-MM-DD（可选）
  remind_at— 提醒时间 HH:MM（循环）或 YYYY-MM-DD HH:MM（一次性）（可选）
  recur    — 循环规则 daily/weekday/weekly/monthly/""（可选）
  recur_spec — 循环细节 {cycle_on, cycle_off, start_date, weekdays}（可选）
"""
import sys
import re
from datetime import datetime, timezone, timedelta, date as _date

BEIJING_TZ = timezone(timedelta(hours=8))

_ID_COUNTER = 0


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _now():
    return datetime.now(BEIJING_TZ)


def _now_str():
    return _now().strftime("%Y-%m-%d")


def _next_id():
    global _ID_COUNTER
    _ID_COUNTER += 1
    now = _now()
    return f"t_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{_ID_COUNTER}"


# ============================================================
# Todo.md 解析 / 重建（Obsidian 展示层）
# ============================================================

def _parse_todo_md(text):
    """
    解析 Todo.md，返回 (doing_items, done_items)。
    兼容新旧两种格式：
      旧格式：## 进行中 / ## 已完成
      新格式：## 🔁 每日习惯 / ## 📌 进行中 / ## ✅ 已完成
    每个 item 是 {"raw": str, "content": str, "date": str, "checked": bool,
                   "due_date": str, "remind_at": str, "recur_raw": str}
    """
    doing = []
    done = []
    current_section = None

    for line in text.split("\n"):
        stripped = line.strip()
        # 新格式分区
        if stripped.startswith("## 🔁") or stripped.startswith("## 📌"):
            current_section = "doing"
            continue
        elif stripped.startswith("## ✅"):
            current_section = "done"
            continue
        # 旧格式兼容
        elif stripped.startswith("## 进行中"):
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

        checked = stripped.startswith("- [x]")

        # 提取各种标签
        content = stripped
        date_tag = ""
        due_date = ""
        remind_at = ""
        recur_raw = ""

        # 创建日期 `YYYY-MM-DD`
        dm = re.search(r'`(\d{4}-\d{2}-\d{2})`', content)
        if dm:
            date_tag = dm.group(1)
            content = content.replace(dm.group(0), "")

        # 截止日期 📅 YYYY-MM-DD
        dd = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', content)
        if dd:
            due_date = dd.group(1)
            content = content.replace(dd.group(0), "")

        # 提醒时间 ⏰ YYYY-MM-DD HH:MM 或 ⏰ HH:MM
        rt = re.search(r'⏰\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})', content)
        if rt:
            remind_at = rt.group(1).strip()
            content = content.replace(rt.group(0), "")

        # 循环标记 🔁 ...
        rc = re.search(r'🔁\s*(.+?)(?=📅|⏰|`\d{4}|$)', content)
        if rc:
            recur_raw = rc.group(1).strip()
            content = content.replace(rc.group(0), "")

        # 完成标记 ✅
        content = re.sub(r'✅\s*', '', content)

        # 去掉 checkbox 标记
        content = re.sub(r'^- \[[ x]\]\s*', '', content).strip()

        item = {
            "raw": stripped, "content": content, "date": date_tag,
            "checked": checked, "due_date": due_date,
            "remind_at": remind_at, "recur_raw": recur_raw,
        }
        if current_section == "doing":
            doing.append(item)
        elif current_section == "done":
            done.append(item)

    return doing, done


def _build_todo_line(todo, show_checkin=False):
    """从 state todo 对象构建 Todo.md 行。
    show_checkin=True 时，为今日已打卡的循环待办添加 ✅今日已打卡 标记。
    """
    parts = [f"- [ ] {todo['content']}"]
    if todo.get("recur"):
        recur_text = _recur_display(todo)
        parts.append(f" 🔁 {recur_text}")
        # 打卡状态可视化
        if show_checkin and todo.get("last_completed") == _now_str():
            parts.append(" ✅今日已打卡")
    if todo.get("due_date"):
        parts.append(f" 📅 {todo['due_date']}")
    if todo.get("remind_at") and not todo.get("recur"):
        # 循环待办的 remind_at 已在 🔁 文本中显示，不重复
        parts.append(f" ⏰ {todo['remind_at']}")
    parts.append(f" `{todo.get('created', _now_str())}`")
    return "".join(parts)


def _recur_display(todo):
    """生成循环规则的人类可读文本"""
    recur = todo.get("recur", "")
    spec = todo.get("recur_spec") or {}
    remind = todo.get("remind_at", "")
    time_part = f" {remind}" if remind and len(remind) <= 5 else ""

    if recur == "daily":
        base = f"每天{time_part}"
        if spec.get("cycle_on") and spec.get("cycle_off"):
            base += f" ({spec['cycle_on']}天/停{spec['cycle_off']}天)"
        return base
    elif recur == "weekday":
        return f"工作日{time_part}"
    elif recur == "weekly":
        if spec.get("weekdays"):
            day_names = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
            days = "、".join(day_names.get(d, str(d)) for d in spec["weekdays"])
            return f"每周{days}{time_part}"
        return f"每周{time_part}"
    elif recur == "monthly":
        day = spec.get("day", "")
        return f"每月{day}号{time_part}" if day else f"每月{time_part}"
    elif recur == "custom":
        interval = spec.get("interval", 1)
        unit = spec.get("unit", "天")
        return f"每{interval}{unit}{time_part}"
    return recur


def _rebuild_todo_md(doing_items, done_items):
    """重建 Todo.md 文件内容。
    分区显示：🔁每日习惯 / 📌进行中 / ✅已完成。
    自动将"进行中"区域的已勾选 [x] 项移到"已完成"头部。
    """
    # 清理：将 doing 中已打勾的项移到 done
    clean_doing = []
    moved_to_done = []
    for item in doing_items:
        raw = item.get("raw", "")
        if raw.strip().startswith("- [x]"):
            moved_to_done.append(item)
        else:
            clean_doing.append(item)

    # 移动的项放到已完成列表头部
    all_done = moved_to_done + done_items

    # 分区：循环待办 vs 一次性待办
    recur_items = []
    onetime_items = []
    for item in clean_doing:
        if "🔁" in item.get("raw", ""):
            recur_items.append(item)
        else:
            onetime_items.append(item)

    lines = ["# 📋 待办清单", ""]

    # 循环习惯区（有内容才显示）
    if recur_items:
        lines.append("## 🔁 每日习惯")
        for item in recur_items:
            lines.append(item["raw"])
        lines.append("")

    # 一次性任务区
    lines.append("## 📌 进行中")
    for item in onetime_items:
        lines.append(item["raw"])
    lines.append("")

    # 已完成区
    done_count = len(all_done)
    if done_count > 0:
        lines.append(f"## ✅ 已完成（最近 {min(done_count, MAX_DONE_DISPLAY)} 条）")
    else:
        lines.append("## ✅ 已完成")
    for item in all_done:
        lines.append(item["raw"])
    lines.append("")
    return "\n".join(lines)


MAX_DONE_DISPLAY = 30  # Todo.md 中已完成区域最多保留条数


def _sync_todo_md(todos, done_items, ctx, todo_file):
    """根据 state.todos 重新生成 Todo.md（保留已完成区域）。
    增强：去重 + 已完成区上限 + 自动归档 + 打卡可视化。
    """
    doing = []
    seen = set()
    for t in todos:
        key = t.get("content", "").strip().lower()
        if key in seen:
            continue  # 跳过重复内容
        seen.add(key)
        doing.append({"raw": _build_todo_line(t, show_checkin=True), "content": t["content"]})

    # 已完成区域去重（按 content 去重，保留最新的）
    done_seen = set()
    unique_done = []
    for item in done_items:
        key = item.get("content", "").strip().lower()
        if key in done_seen:
            continue
        done_seen.add(key)
        unique_done.append(item)

    # 已完成超出上限时归档到 Todo-Archive.md
    if len(unique_done) > MAX_DONE_DISPLAY:
        archive_items = unique_done[MAX_DONE_DISPLAY:]
        unique_done = unique_done[:MAX_DONE_DISPLAY]
        _archive_done(archive_items, ctx, todo_file)

    text = _rebuild_todo_md(doing, unique_done)
    ctx.IO.write_text(todo_file, text)


def _archive_done(items, ctx, todo_file):
    """将溢出的已完成条目追加到 Todo-Archive.md"""
    try:
        archive_file = todo_file.replace("Todo.md", "Todo-Archive.md")
        existing = ctx.IO.read_text(archive_file) or "# 📦 待办归档\n\n"
        new_lines = "\n".join(item.get("raw", "") for item in items if item.get("raw"))
        if new_lines:
            ctx.IO.write_text(archive_file, existing.rstrip("\n") + "\n" + new_lines + "\n")
            _log(f"[todo.archive] 归档 {len(items)} 条已完成待办到 Todo-Archive.md")
    except Exception as e:
        _log(f"[todo.archive] 归档失败: {e}")


# ============================================================
# 循环规则判定
# ============================================================

def _should_trigger_today(todo, now=None):
    """判断循环待办今天是否应该触发"""
    if not now:
        now = _now()
    today = now.date() if hasattr(now, 'date') else now
    recur = todo.get("recur", "")
    spec = todo.get("recur_spec") or {}

    if recur == "daily":
        # 检查 cycle_on / cycle_off
        if spec.get("cycle_on") and spec.get("cycle_off"):
            return _is_active_day(spec, today)
        return True

    elif recur == "weekday":
        return today.weekday() < 5  # 0=Mon ... 4=Fri

    elif recur == "weekly":
        if spec.get("weekdays"):
            return today.isoweekday() in spec["weekdays"]  # 1=Mon ... 7=Sun
        # 默认按创建日的星期
        created = spec.get("start_date") or todo.get("created", "")
        if created:
            try:
                cd = datetime.strptime(created, "%Y-%m-%d").date()
                return today.weekday() == cd.weekday()
            except ValueError:
                pass
        return True

    elif recur == "monthly":
        day = spec.get("day")
        if day:
            return today.day == int(day)
        created = spec.get("start_date") or todo.get("created", "")
        if created:
            try:
                cd = datetime.strptime(created, "%Y-%m-%d").date()
                return today.day == cd.day
            except ValueError:
                pass
        return True

    elif recur == "custom":
        interval = spec.get("interval", 1)
        start = spec.get("start_date") or todo.get("created", "")
        if start:
            try:
                sd = datetime.strptime(start, "%Y-%m-%d").date()
                return (today - sd).days % interval == 0
            except ValueError:
                pass
        return True

    return False


def _is_active_day(spec, today):
    """判断今天是否在 cycle_on 窗口内（如 24天吃/4天停）"""
    start_str = spec.get("start_date", "")
    if not start_str:
        return True
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
    except ValueError:
        return True
    cycle_on = spec.get("cycle_on", 1)
    cycle_off = spec.get("cycle_off", 0)
    total = cycle_on + cycle_off
    if total <= 0:
        return True
    day_in_cycle = (today - start).days % total
    return day_in_cycle < cycle_on


# ============================================================
# 迁移：state.reminders → state.todos
# ============================================================

def _migrate_reminders_to_todos(state, ctx=None, todo_file=None):
    """
    一次性迁移：将旧 state.reminders 转为 state.todos，
    同时扫描 Todo.md 中的 🔁 标记注册循环待办。
    迁移后删除 state.reminders。
    """
    if "todos" in state:
        return False  # 已迁移

    todos = []
    migrated_contents = set()

    # 1) 迁移旧 reminders
    old_reminders = state.get("reminders", [])
    for i, r in enumerate(old_reminders):
        content = r.get("content", "").strip()
        if not content:
            continue
        # 旧格式 day_notified/notified 可能是 "YYYY-MM-DD HH:MM"，截断为日期
        old_notified = r.get("day_notified", r.get("notified", ""))
        if old_notified:
            old_notified = old_notified[:10]  # "2026-03-01 09:00" → "2026-03-01"
        t = {
            "id": f"t_migrated_{i}",
            "content": content,
            "created": r.get("created", ""),
            "due_date": r.get("due_date", ""),
            "remind_at": r.get("remind_at", ""),
            "recur": "",
            "recur_spec": {},
            "last_notified": old_notified,
            "last_completed": "",
        }
        todos.append(t)
        migrated_contents.add(content.lower())

    # 2) 扫描 Todo.md 中的 🔁 标记
    if ctx and todo_file:
        try:
            text = ctx.IO.read_text(todo_file)
            if text:
                doing, _ = _parse_todo_md(text)
                for item in doing:
                    if not item.get("recur_raw"):
                        continue
                    if item["content"].lower() in migrated_contents:
                        # 已有的 reminder 补上循环信息
                        for t in todos:
                            if t["content"].lower() == item["content"].lower():
                                recur, spec, recur_time = _parse_recur_raw(item["recur_raw"])
                                t["recur"] = recur
                                t["recur_spec"] = spec
                                # 优先用 🔁 文本中的时间，其次用 ⏰ 标签
                                if recur_time:
                                    t["remind_at"] = recur_time
                                elif item.get("remind_at") and len(item["remind_at"]) <= 5:
                                    t["remind_at"] = item["remind_at"]
                                break
                    else:
                        # Todo.md 中有但 state 中没有的循环待办
                        recur, spec, recur_time = _parse_recur_raw(item["recur_raw"])
                        remind = recur_time or item.get("remind_at", "")
                        t = {
                            "id": _next_id(),
                            "content": item["content"],
                            "created": item.get("date", _now_str()),
                            "due_date": item.get("due_date", ""),
                            "remind_at": remind,
                            "recur": recur,
                            "recur_spec": spec,
                            "last_notified": "",
                            "last_completed": "",
                        }
                        todos.append(t)

                    # 同时注册普通待办（无循环无提醒的）
                for item in doing:
                    if item.get("recur_raw") or item.get("checked"):
                        continue
                    if item["content"].lower() in migrated_contents:
                        continue
                    # 检查是否已在 todos 中
                    already = False
                    for t in todos:
                        if t["content"].lower() == item["content"].lower():
                            already = True
                            break
                    if already:
                        continue
                    t = {
                        "id": _next_id(),
                        "content": item["content"],
                        "created": item.get("date", _now_str()),
                        "due_date": item.get("due_date", ""),
                        "remind_at": item.get("remind_at", ""),
                        "recur": "",
                        "recur_spec": {},
                        "last_notified": "",
                        "last_completed": "",
                    }
                    todos.append(t)
        except Exception as e:
            _log(f"[todo.migrate] 扫描 Todo.md 失败: {e}")

    state["todos"] = todos
    state.pop("reminders", None)
    _log(f"[todo.migrate] 迁移完成: {len(old_reminders)} 条 reminders + Todo.md → {len(todos)} 条 todos")
    return True


def _parse_recur_raw(raw):
    """
    解析 Todo.md 中 🔁 后的原始文本，返回 (recur, recur_spec, remind_time)。
    remind_time 是从文本中提取的 HH:MM 时间（如 "14:10"），用于 remind_at。
    示例:
      "每天 14:10 (24天/停4天)" → ("daily", {"cycle_on":24, "cycle_off":4}, "14:10")
      "工作日 17:30" → ("weekday", {}, "17:30")
      "每周一、三、五" → ("weekly", {"weekdays":[1,3,5]}, "")
      "每月15号" → ("monthly", {"day":15}, "")
    """
    raw = raw.strip()
    spec = {}

    # 提取时间 HH:MM
    time_m = re.search(r'(\d{1,2}:\d{2})', raw)
    remind_time = time_m.group(1) if time_m else ""
    # 补零：9:00 → 09:00
    if remind_time and len(remind_time) == 4:
        remind_time = "0" + remind_time

    # 周期模式 (N天/停M天)
    cycle_m = re.search(r'\((\d+)天[/／]停(\d+)天\)', raw)
    if cycle_m:
        spec["cycle_on"] = int(cycle_m.group(1))
        spec["cycle_off"] = int(cycle_m.group(2))

    if raw.startswith("每天") or raw.startswith("每日"):
        return "daily", spec, remind_time
    elif raw.startswith("工作日"):
        return "weekday", spec, remind_time
    elif raw.startswith("每周"):
        # 解析星期几
        day_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        days = []
        for ch, num in day_map.items():
            if ch in raw:
                days.append(num)
        if days:
            spec["weekdays"] = sorted(set(days))
        return "weekly", spec, remind_time
    elif raw.startswith("每月"):
        dm = re.search(r'(\d+)号?', raw)
        if dm:
            spec["day"] = int(dm.group(1))
        return "monthly", spec, remind_time
    elif re.search(r'每(\d+)天', raw):
        m = re.search(r'每(\d+)天', raw)
        spec["interval"] = int(m.group(1))
        spec["unit"] = "天"
        return "custom", spec, remind_time

    # 降级：无法解析的当作每天
    if "天" in raw or "日" in raw:
        return "daily", spec, remind_time
    return "daily", spec, remind_time


# ============================================================
# Skill 入口
# ============================================================

def add(params, state, ctx):
    """
    添加待办事项。

    params:
        content: str — 待办内容
        due_date: str — 可选，截止日期 YYYY-MM-DD
        remind_at: str — 可选，提醒时间 HH:MM（循环）或 YYYY-MM-DD HH:MM（一次性）
        recur: str — 可选，循环规则 daily/weekday/weekly/monthly
        recur_spec: dict — 可选，循环细节 {cycle_on, cycle_off, start_date, weekdays, day, interval, unit}
    """
    content = (params.get("content") or "").strip()
    if not content:
        return {"success": False, "reply": "待办内容不能为空"}

    due_date = (params.get("due_date") or "").strip()
    remind_at = (params.get("remind_at") or "").strip()
    recur = (params.get("recur") or "").strip()
    recur_spec = params.get("recur_spec") or {}

    # 自动迁移
    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    # ── 去重检查：相同内容不重复添加 ──
    todos = state.get("todos", [])
    for t in todos:
        if t.get("content", "").strip().lower() == content.lower():
            return {"success": False, "reply": f"已存在相同待办「{t['content']}」，无需重复添加"}

    # 如果有循环但 start_date 没填，默认今天
    if recur and "start_date" not in recur_spec:
        recur_spec["start_date"] = _now_str()

    todo = {
        "id": _next_id(),
        "content": content,
        "created": _now_str(),
        "due_date": due_date,
        "remind_at": remind_at,
        "recur": recur,
        "recur_spec": recur_spec,
        "last_notified": "",
        "last_completed": "",
    }

    todos = state.get("todos", [])
    todos.append(todo)

    # 写 Todo.md
    text = ctx.IO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}
    if not text.strip():
        text = "# 📋 待办清单\n\n## 进行中\n\n## 已完成\n"
    doing, done = _parse_todo_md(text)

    new_line = _build_todo_line(todo)
    doing.append({"raw": new_line, "content": content, "date": _now_str()})

    new_text = _rebuild_todo_md(doing, done)
    ok = ctx.IO.write_text(ctx.todo_file, new_text)

    if ok:
        _log(f"[todo.add] 已添加: {content}" + (f" recur={recur}" if recur else ""))
        return {"success": True, "state_updates": {"todos": todos}}
    else:
        return {"success": False, "reply": "写入 Todo.md 失败"}


def complete(params, state, ctx):
    """
    完成待办事项，支持关键词匹配和序号批量完成。
    循环待办只标记今天完成（打卡），不移到已完成区域。

    params:
        keyword: str — 用于匹配待办的关键词
        indices: str — 用序号完成，支持 "3" / "2-7" / "1,3,5"
    """
    keyword = (params.get("keyword") or "").strip().lower()
    indices_str = (params.get("indices") or "").strip()

    if not keyword and not indices_str:
        return {"success": False, "reply": "请告诉我要完成哪个待办"}

    # 自动迁移
    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    text = ctx.IO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)
    todos = state.get("todos", [])

    if indices_str:
        # ── 序号模式：批量完成 ──
        target_indices = _parse_indices(indices_str, len(doing))
        if not target_indices:
            return {"success": False, "reply": f"无法解析序号「{indices_str}」，或序号超出范围"}

        completed_names = []
        checkin_names = []
        for idx in sorted(target_indices, reverse=True):
            if 0 <= idx < len(doing):
                item = doing[idx]
                matched_todo = _find_todo_by_content(todos, item["content"])

                if matched_todo and matched_todo.get("recur"):
                    # 循环待办：打卡，不移到已完成
                    matched_todo["last_completed"] = _now_str()
                    checkin_names.append(item["content"])
                else:
                    # 一次性待办：移到已完成
                    popped = doing.pop(idx)
                    done_line = popped["raw"].replace("- [ ]", "- [x]")
                    if f"`{_now_str()}`" not in done_line:
                        done_line += f" ✅ `{_now_str()}`"
                    done.insert(0, {"raw": done_line, "content": popped["content"], "date": _now_str()})
                    completed_names.append(popped["content"])
                    # 从 state.todos 移除
                    if matched_todo:
                        todos.remove(matched_todo)

        if not completed_names and not checkin_names:
            return {"success": False, "reply": "没有找到对应序号的待办"}

        new_text = _rebuild_todo_md(doing, done)
        ok = ctx.IO.write_text(ctx.todo_file, new_text)

        if ok:
            parts = []
            if completed_names:
                names = "、".join(f"「{c[:20]}」" for c in completed_names)
                parts.append(f"已完成 {len(completed_names)} 条待办 ✅\n{names}")
            if checkin_names:
                names = "、".join(f"「{c[:20]}」" for c in checkin_names)
                parts.append(f"今日打卡 {len(checkin_names)} 条 🔁\n{names}")
            _log(f"[todo.done] 批量: done={len(completed_names)}, checkin={len(checkin_names)}")
            return {"success": True, "reply": "\n".join(parts), "state_updates": {"todos": todos}}
        return {"success": False, "reply": "写入 Todo.md 失败"}

    else:
        # ── 关键词模式：单条匹配 ──
        matched_idx = -1
        for i, item in enumerate(doing):
            if keyword in item["content"].lower():
                matched_idx = i
                break

        if matched_idx < 0:
            return {"success": False, "reply": f"没找到包含「{keyword}」的待办"}

        item = doing[matched_idx]
        matched_todo = _find_todo_by_content(todos, item["content"])

        if matched_todo and matched_todo.get("recur"):
            # 循环待办：打卡
            matched_todo["last_completed"] = _now_str()
            _log(f"[todo.done] 循环打卡: {item['content']}")
            return {
                "success": True,
                "reply": f"今天的「{item['content']}」已打卡 ✅🔁",
                "state_updates": {"todos": todos},
            }
        else:
            # 一次性待办：移到已完成
            doing.pop(matched_idx)
            done_line = item["raw"].replace("- [ ]", "- [x]")
            if f"`{_now_str()}`" not in done_line:
                done_line += f" ✅ `{_now_str()}`"
            done.insert(0, {"raw": done_line, "content": item["content"], "date": _now_str()})

            if matched_todo:
                todos.remove(matched_todo)

            new_text = _rebuild_todo_md(doing, done)
            ok = ctx.IO.write_text(ctx.todo_file, new_text)
            if ok:
                _log(f"[todo.done] 已完成: {item['content']}")
                return {
                    "success": True,
                    "reply": f"已完成「{item['content']}」✅",
                    "state_updates": {"todos": todos},
                }
            return {"success": False, "reply": "写入 Todo.md 失败"}


def _find_todo_by_content(todos, content):
    """在 state.todos 中按内容模糊匹配"""
    kw = content.lower()
    for t in todos:
        tc = t.get("content", "").lower()
        if kw in tc or tc in kw:
            return t
    return None


def _parse_indices(s, max_len):
    """
    解析序号字符串，返回 0-based 索引列表。
    支持: "3" / "2-7" / "1,3,5" / "2、4、6" / "2到7" / "2~7"
    """
    s = s.replace("、", ",").replace("到", "-").replace("~", "-").replace("～", "-")
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


def list_todos(params, state, ctx):
    """
    查看待办清单（带序号，用户可用序号引用）。
    循环待办标注 🔁，显示今日是否已打卡。
    """
    # 自动迁移
    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    text = ctx.IO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)
    todos = state.get("todos", [])
    today = _now_str()

    parts = []
    if doing:
        parts.append("📋 进行中:")
        for i, item in enumerate(doing, 1):
            line = f"  {i}. {item['content']}"
            # 查找对应的 state todo，标注循环和打卡状态
            matched = _find_todo_by_content(todos, item["content"])
            if matched and matched.get("recur"):
                if matched.get("last_completed") == today:
                    line += " ✅🔁"
                else:
                    line += " 🔁"
            elif item.get("due_date"):
                line += f" 📅{item['due_date']}"
            parts.append(line)
    else:
        parts.append("📋 没有进行中的待办")

    if done:
        recent_done = done[:3]
        parts.append(f"\n✅ 最近完成 ({len(done)} 条):")
        for item in recent_done:
            parts.append(f"  · {item['content']}")

    result = {"success": True, "reply": "\n".join(parts)}
    # 如果迁移产生了 state 变更，一并返回
    if "todos" in state:
        result["state_updates"] = {"todos": todos}
    return result


# ============================================================
# 提醒检查引擎（替代旧 check_reminders）
# ============================================================

def check_todos(state, ctx=None, todo_file=None):
    """
    检查到期待办，返回需要推送的消息列表。
    由 /system?action=todo_remind 直接调用，不经过 LLM。

    逻辑：
    1. 自动迁移 state.reminders → state.todos
    2. 交叉验证 Todo.md（清理已手动完成的待办）
    3. 循环待办：判断今天是否在活跃周期 + 是否到提醒时间
    4. 一次性定时提醒：到时间就推
    5. 截止日期提醒：当天推 + 过期推
    6. 清理过期一次性待办（>30天）

    返回: {"messages": [str], "state_updates": dict}
    """
    # 自动迁移
    migrated = _migrate_reminders_to_todos(state, ctx, todo_file)

    todos = state.get("todos", [])
    if not todos:
        return {"messages": [], "state_updates": {}}

    # ── 交叉验证 Todo.md ──
    cross_cleaned = 0
    if todo_file and ctx:
        try:
            text = ctx.IO.read_text(todo_file)
            if text:
                doing, _ = _parse_todo_md(text)
                active_contents = [item["content"].lower() for item in doing if not item.get("checked")]
                before = len(todos)
                # 只清理一次性待办（循环待办始终保留在 state 中）
                new_todos = []
                for t in todos:
                    if t.get("recur"):
                        new_todos.append(t)  # 循环待办不清理
                    elif _content_in_list(t["content"], active_contents):
                        new_todos.append(t)
                    else:
                        cross_cleaned += 1
                todos = new_todos
                if cross_cleaned > 0:
                    _log(f"[todo.check] 交叉验证清理 {cross_cleaned} 条已手动完成的待办")
        except Exception as e:
            _log(f"[todo.check] 读取 Todo.md 交叉验证失败: {e}")

    now = _now()
    today_str = now.strftime("%Y-%m-%d")
    messages = []
    changed = migrated or cross_cleaned > 0

    for t in todos:
        content = t.get("content", "")
        remind_at = t.get("remind_at", "")
        due_date = t.get("due_date", "")
        recur = t.get("recur", "")

        # ── 循环待办 ──
        if recur:
            if t.get("last_notified") == today_str:
                continue  # 今天已推送
            if not _should_trigger_today(t, now):
                continue  # 不在活跃周期

            if remind_at and len(remind_at) <= 5:
                # HH:MM 格式 — 到时间才推
                try:
                    h, m = remind_at.split(":")
                    remind_time = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                    if now >= remind_time:
                        messages.append(f"🔁 提醒：{content}")
                        t["last_notified"] = today_str
                        changed = True
                except (ValueError, TypeError):
                    pass
            else:
                # 无具体时间 — 直接推
                messages.append(f"🔁 今日待办：{content}")
                t["last_notified"] = today_str
                changed = True
            continue

        # ── 一次性定时提醒 ──
        if remind_at and len(remind_at) > 5:
            if t.get("last_notified"):
                continue  # 已推送过
            try:
                remind_time = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
                remind_time = remind_time.replace(tzinfo=BEIJING_TZ)
                diff_minutes = (remind_time - now).total_seconds() / 60
                if diff_minutes <= 0:
                    messages.append(f"⏰ 提醒：{content}")
                    t["last_notified"] = today_str
                    changed = True
                elif diff_minutes <= 30 and not t.get("pre_notified"):
                    messages.append(f"⏰ {int(diff_minutes)} 分钟后：{content}")
                    t["pre_notified"] = today_str
                    changed = True
            except ValueError:
                pass
            continue

        # ── 截止日期提醒 ──
        if due_date:
            if due_date == today_str and t.get("last_notified") != today_str:
                messages.append(f"📅 今天截止：{content}")
                t["last_notified"] = today_str
                changed = True
            elif due_date < today_str and not t.get("overdue_notified"):
                messages.append(f"⚠️ 已过期：{content}（截止 {due_date}）")
                t["overdue_notified"] = today_str
                changed = True

    # ── 清理过期一次性待办（已通知且过期 >30天） ──
    cleaned = []
    for t in todos:
        if not t.get("recur"):
            due = t.get("due_date", "")
            notified = t.get("last_notified", "")
            if notified and due and due < today_str:
                try:
                    due_dt = datetime.strptime(due, "%Y-%m-%d").date()
                    if (now.date() - due_dt).days > 30:
                        _log(f"[todo.check] 清理过期待办: {t['content']}")
                        continue
                except ValueError:
                    pass
        cleaned.append(t)

    if len(cleaned) != len(todos):
        changed = True
        todos = cleaned

    state_updates = {}
    if changed:
        state["todos"] = todos
        state_updates["todos"] = todos

    _log(f"[todo.check] 检查 {len(todos)} 条待办, 推送 {len(messages)} 条")
    return {"messages": messages, "state_updates": state_updates}


def _content_in_list(content, content_list):
    """检查 content 是否在列表中（双向模糊匹配）"""
    kw = content.lower()
    for c in content_list:
        if kw in c or c in kw:
            return True
    return False


# ── 兼容旧调用 ──
def check_reminders(state, ctx=None, todo_file=None):
    """向后兼容：内部转发到 check_todos"""
    return check_todos(state, ctx=ctx, todo_file=todo_file)


def check_precise_reminders(state, ctx=None, todo_file=None):
    """
    精准提醒检查（每分钟调用）— 只检查当前分钟精确匹配的提醒。

    与 check_todos 的区别：
    - check_todos：30分钟一次，走意图系统，检查所有类型（截止日期、过期、循环无时间等）
    - check_precise_reminders：1分钟一次，独立通道，只匹配有精确 HH:MM 的提醒

    不读 Todo.md，不做迁移/交叉验证/过期清理，极轻量。
    与 check_todos 共享 last_notified 防重复。
    """
    todos = state.get("todos", [])
    if not todos:
        return {"messages": [], "state_updates": {}}

    now = _now()
    now_hm = now.strftime("%H:%M")
    today_str = now.strftime("%Y-%m-%d")
    messages = []
    changed = False

    for t in todos:
        remind_at = t.get("remind_at", "")
        if not remind_at:
            continue

        recur = t.get("recur", "")

        # ── 循环待办：HH:MM 格式 ──
        if recur and len(remind_at) <= 5:
            if t.get("last_notified") == today_str:
                continue  # 今天已推送（可能被 check_todos 先推了）
            if not _should_trigger_today(t, now):
                continue
            if remind_at == now_hm:
                messages.append(f"🔁 提醒：{t['content']}")
                t["last_notified"] = today_str
                changed = True

        # ── 一次性定时提醒：YYYY-MM-DD HH:MM 格式 ──
        elif not recur and len(remind_at) > 5:
            if t.get("last_notified"):
                continue  # 已推送过
            try:
                remind_dt = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
                remind_date = remind_dt.strftime("%Y-%m-%d")
                remind_hm = remind_dt.strftime("%H:%M")
                if remind_date == today_str and remind_hm == now_hm:
                    messages.append(f"⏰ 提醒：{t['content']}")
                    t["last_notified"] = today_str
                    changed = True
            except ValueError:
                pass

    state_updates = {}
    if changed:
        state["todos"] = todos
        state_updates["todos"] = todos

    if messages:
        _log(f"[precise_remind] 精准匹配 {len(messages)} 条提醒 (当前 {now_hm})")

    return {"messages": messages, "state_updates": state_updates}




def edit(params, state, ctx):
    """
    修改待办事项的内容、截止日期、提醒时间、循环规则。

    params:
        keyword: str — 匹配关键词（定位要改哪条）
        index: int — 或用序号定位（1-based，与 todo.list 一致）
        new_content: str — 新内容（可选）
        new_due_date: str — 新截止日期 YYYY-MM-DD（可选，传 "" 清除）
        new_remind_at: str — 新提醒时间（可选，传 "" 清除）
        new_recur: str — 新循环规则（可选，传 "" 清除循环）
        new_recur_spec: dict — 新循环细节（可选）
    """
    keyword = (params.get("keyword") or "").strip().lower()
    index = params.get("index")  # 1-based
    if not keyword and index is None:
        return {"success": False, "reply": "请告诉我要修改哪个待办"}

    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    text = ctx.IO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)
    todos = state.get("todos", [])

    # 定位目标
    matched_item = None
    matched_todo = None
    if index is not None:
        idx = int(index) - 1
        if 0 <= idx < len(doing):
            matched_item = doing[idx]
            matched_todo = _find_todo_by_content(todos, matched_item["content"])
    else:
        for item in doing:
            if keyword in item["content"].lower():
                matched_item = item
                matched_todo = _find_todo_by_content(todos, item["content"])
                break

    if not matched_item:
        return {"success": False, "reply": f"没找到匹配的待办"}

    if not matched_todo:
        return {"success": False, "reply": f"找到了 Todo.md 中的条目，但 state 中无对应数据，请先查看待办列表"}

    # 应用修改
    changes = []
    if "new_content" in params and params["new_content"]:
        old = matched_todo["content"]
        matched_todo["content"] = params["new_content"].strip()
        changes.append(f"内容: {old} → {matched_todo['content']}")

    if "new_due_date" in params:
        val = (params["new_due_date"] or "").strip()
        matched_todo["due_date"] = val
        changes.append(f"截止日期: {val or '已清除'}")

    if "new_remind_at" in params:
        val = (params["new_remind_at"] or "").strip()
        matched_todo["remind_at"] = val
        changes.append(f"提醒时间: {val or '已清除'}")

    if "new_recur" in params:
        val = (params["new_recur"] or "").strip()
        matched_todo["recur"] = val
        if not val:
            matched_todo["recur_spec"] = {}
        changes.append(f"循环规则: {val or '已清除'}")

    if "new_recur_spec" in params and params["new_recur_spec"]:
        matched_todo["recur_spec"] = params["new_recur_spec"]
        changes.append("循环细节已更新")

    if not changes:
        return {"success": False, "reply": "没有指定要修改的内容"}

    # 同步 Todo.md
    _sync_todo_md(todos, done, ctx, ctx.todo_file)

    changes_str = "、".join(changes)
    _log(f"[todo.edit] 已修改「{matched_todo['content']}」: {changes_str}")
    return {
        "success": True,
        "reply": f"已修改「{matched_todo['content']}」✏️\n{changes_str}",
        "state_updates": {"todos": todos},
    }


def delete(params, state, ctx):
    """
    删除（废弃）待办事项，不记入"已完成"。

    params:
        keyword: str — 匹配关键词
        indices: str — 序号批量删除，支持 "3" / "2-7" / "1,3,5"
    """
    keyword = (params.get("keyword") or "").strip().lower()
    indices_str = (params.get("indices") or "").strip()

    if not keyword and not indices_str:
        return {"success": False, "reply": "请告诉我要删除哪个待办"}

    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    text = ctx.IO.read_text(ctx.todo_file)
    if text is None:
        return {"success": False, "reply": "读取 Todo.md 失败"}

    doing, done = _parse_todo_md(text)
    todos = state.get("todos", [])

    deleted_names = []

    if indices_str:
        # 序号模式
        target_indices = _parse_indices(indices_str, len(doing))
        if not target_indices:
            return {"success": False, "reply": f"无法解析序号「{indices_str}」，或序号超出范围"}

        for idx in sorted(target_indices, reverse=True):
            if 0 <= idx < len(doing):
                item = doing.pop(idx)
                deleted_names.append(item["content"])
                matched = _find_todo_by_content(todos, item["content"])
                if matched:
                    todos.remove(matched)
    else:
        # 关键词模式
        matched_idx = -1
        for i, item in enumerate(doing):
            if keyword in item["content"].lower():
                matched_idx = i
                break

        if matched_idx < 0:
            return {"success": False, "reply": f"没找到包含「{keyword}」的待办"}

        item = doing.pop(matched_idx)
        deleted_names.append(item["content"])
        matched = _find_todo_by_content(todos, item["content"])
        if matched:
            todos.remove(matched)

    if not deleted_names:
        return {"success": False, "reply": "没有找到要删除的待办"}

    # 重建 Todo.md（不写入已完成区域）
    new_text = _rebuild_todo_md(doing, done)
    ok = ctx.IO.write_text(ctx.todo_file, new_text)

    if ok:
        names = "、".join(f"「{c[:20]}」" for c in deleted_names)
        _log(f"[todo.delete] 已删除 {len(deleted_names)} 条: {names}")
        return {
            "success": True,
            "reply": f"已删除 {len(deleted_names)} 条待办 🗑️\n{names}",
            "state_updates": {"todos": todos},
        }
    return {"success": False, "reply": "写入 Todo.md 失败"}


def remind_cancel(params, state, ctx):
    """取消循环待办（清除循环标记）

    params:
        id: str - 精确匹配 todo ID
        content: str - 模糊匹配内容
    """
    _migrate_reminders_to_todos(state, ctx, ctx.todo_file)

    target_id = (params.get("id") or "").strip()
    target_content = (params.get("content") or "").strip().lower()
    todos = state.get("todos", [])
    cancelled = []

    for t in todos:
        if not t.get("recur"):
            continue
        if (target_id and t.get("id") == target_id) or \
           (target_content and target_content in t.get("content", "").lower()):
            t["recur"] = ""
            t["recur_spec"] = {}
            cancelled.append(t["content"])

    if cancelled:
        names = "、".join(f"「{c}」" for c in cancelled)
        _log(f"[todo.remind_cancel] 已取消: {names}")

        try:
            text = ctx.IO.read_text(ctx.todo_file)
            if text:
                doing, done = _parse_todo_md(text)
                for item in doing:
                    for c in cancelled:
                        if c.lower() in item["content"].lower() or item["content"].lower() in c.lower():
                            item["raw"] = re.sub(r" ?\U0001f501[^`]*", "", item["raw"])
                            break
                new_text = _rebuild_todo_md(doing, done)
                ctx.IO.write_text(ctx.todo_file, new_text)
        except Exception as e:
            _log(f"[todo.remind_cancel] 更新 Todo.md 失败: {e}")

        return {
            "success": True,
            "reply": f"已取消循环提醒：{names}",
            "state_updates": {"todos": todos},
        }

    return {"success": False, "reply": "未找到匹配的循环提醒"}

# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "todo.add": add,
    "todo.done": complete,
    "todo.list": list_todos,
    "todo.edit": edit,
    "todo.delete": delete,
    "todo.remind_cancel": remind_cancel,
}
