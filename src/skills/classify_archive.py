# -*- coding: utf-8 -*-
"""
Skill: classify.archive
将消息按分类归档到对应的 Obsidian 笔记目录。
分类由 LLM 在决策时直接给出，无需二次 AI 调用。

分类:
  work → 02-Notes/工作笔记/{YYYY-MM-DD}.md
  emotion → 02-Notes/情感日记/{YYYY-MM-DD}.md
  fun → 02-Notes/生活趣事/{YYYY-MM-DD}.md
  misc → 00-Inbox/碎碎念.md
"""
import sys
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _build_category_map(ctx):
    """动态构建分类映射（依赖用户目录路径）"""
    return {
        "work": {"dir": ctx.work_notes_dir, "emoji": "💼", "label": "工作笔记"},
        "emotion": {"dir": ctx.emotion_notes_dir, "emoji": "💭", "label": "情感日记"},
        "fun": {"dir": ctx.fun_notes_dir, "emoji": "😂", "label": "生活趣事"},
        "misc": {"emoji": "📝", "label": "碎碎念"},
    }


def execute(params, state, ctx):
    """
    将消息归档到对应分类。

    params:
        category: str — work/emotion/fun/misc
        title: str — AI 生成的简短标题（10字以内）
        content: str — 消息内容
        attachment: str — 可选，附件路径
        merge: bool — 可选，为 true 时将内容合并到最近一条同类归档条目
    """
    category = (params.get("category") or "misc").strip().lower()
    title = (params.get("title") or "").strip()
    content = (params.get("content") or "").strip()
    attachment = (params.get("attachment") or "").strip()
    merge = bool(params.get("merge", False))

    if not content and not attachment:
        return {"success": False, "reply": "没有可归档的内容"}

    category_map = _build_category_map(ctx)
    if category not in category_map:
        category = "misc"

    cat_info = category_map[category]
    now = datetime.now(BEIJING_TZ)
    time_str = now.strftime("%Y-%m-%d %H:%M")
    date_str = now.strftime("%Y-%m-%d")

    # ── merge 模式：合并到最近一条归档条目 ──
    if merge and category != "misc":
        last = state.get("last_archive")
        if last and last.get("category") == category:
            file_path = last.get("file_path", f"{cat_info['dir']}/{date_str}.md")
            ok = _merge_to_last_entry(file_path, content, attachment, time_str)
            if ok:
                if title:
                    last["title"] = title
                _log(f"[classify.archive] 已合并到 {cat_info['label']} 最近条目: {content[:40]}")
                return {"success": True}
            else:
                _log(f"[classify.archive] 合并失败，降级为新建条目")

    # ── 正常模式：新建条目 ──
    entry_title = f"### {title}" if title else f"### {time_str}"
    entry_parts = [entry_title, ""]
    if content:
        entry_parts.append(content)
    if attachment:
        relative = _format_attachment(attachment)
        if relative:
            entry_parts.append(relative)
    entry_parts.extend([f"*— {time_str}*", "", "---", ""])
    entry = "\n".join(entry_parts)

    if category == "misc":
        ok = _append_to_misc(ctx.misc_file, entry, time_str, content)
        file_path = None
    else:
        file_path = f"{cat_info['dir']}/{date_str}.md"
        ok = _append_to_dated_file(file_path, date_str, entry, cat_info)

    if ok:
        _log(f"[classify.archive] 已归档到 {cat_info['label']}: {(title or content)[:40]}")
        if category != "misc" and file_path:
            state["last_archive"] = {
                "file_path": file_path,
                "category": category,
                "title": title,
                "timestamp": time_str,
            }
            return {"success": True, "state_updates": {"last_archive": state["last_archive"]}}
        return {"success": True}
    else:
        return {"success": False, "reply": f"归档到{cat_info['label']}失败"}


def _format_attachment(attachment):
    """将附件路径格式化为 Obsidian 嵌入语法"""
    if not attachment:
        return None
    relative = attachment
    if "attachments/" in attachment:
        relative = "attachments/" + attachment.split("attachments/")[-1]
    ext = attachment.rsplit(".", 1)[-1].lower() if "." in attachment else ""
    if ext in ("jpg", "jpeg", "png", "gif", "webp", "mp4", "mov"):
        return f"![[{relative}]]"
    elif ext in ("mp3", "wav", "amr", "silk", "m4a"):
        return f"🔗 [[{relative}]]"
    else:
        return f"📎 [[{relative}]]"


def _merge_to_last_entry(file_path, content, attachment, time_str):
    """
    将补充内容合并到归档文件的最后一个条目中。
    找到最后一个 `*— 时间*` 行，在它前面插入补充内容。
    """
    existing = OneDriveIO.read_text(file_path)
    if not existing or not existing.strip():
        return False

    lines = existing.split("\n")

    # 从后往前找最后一个时间戳行 `*— YYYY-MM-DD HH:MM*`
    insert_idx = None
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("*—") and stripped.endswith("*"):
            insert_idx = i
            break

    if insert_idx is None:
        _log(f"[classify.archive] merge: 找不到时间戳行，合并失败")
        return False

    # 构建要插入的补充内容
    supplement_parts = []
    if content:
        supplement_parts.append(content)
    if attachment:
        formatted = _format_attachment(attachment)
        if formatted:
            supplement_parts.append(formatted)

    if not supplement_parts:
        return False

    # 在时间戳行前插入补充内容，并更新时间戳
    supplement_text = "\n".join(supplement_parts)
    lines.insert(insert_idx, supplement_text)
    # 更新时间戳行（insert 后它往后移了一位）
    lines[insert_idx + 1] = f"*— {time_str} (补充)*"

    new_content = "\n".join(lines)
    return OneDriveIO.write_text(file_path, new_content)


def _append_to_misc(misc_file, entry, time_str, content):
    """追加到碎碎念.md"""
    existing = OneDriveIO.read_text(misc_file)
    if existing is None:
        return False

    if not existing.strip():
        existing = "# 📝 碎碎念\n\n无法被 AI 归类的零散记录。\n\n---\n"

    # 追加条目（在最后一个 --- 之后）
    new_section = f"\n## {time_str}\n\n{content}\n\n---\n"
    new_content = existing.rstrip() + "\n" + new_section

    return OneDriveIO.write_text(misc_file, new_content)


def _append_to_dated_file(file_path, date_str, entry, cat_info):
    """追加到按日期命名的归档文件"""
    existing = OneDriveIO.read_text(file_path)
    if existing is None:
        return False

    if not existing.strip():
        # 新建文件
        existing = f"# {cat_info['emoji']} {cat_info['label']} — {date_str}\n\n---\n"

    new_content = existing.rstrip() + "\n\n" + entry
    return OneDriveIO.write_text(file_path, new_content)


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "classify.archive": execute,
}
