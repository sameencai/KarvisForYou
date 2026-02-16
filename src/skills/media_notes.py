# -*- coding: utf-8 -*-
"""
Skill: media.*
影视笔记系统：创建影视笔记、添加感想。
"""
import sys
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _now_str():
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _media_file(name, ctx):
    return f"{ctx.media_notes_dir}/{name}.md"


def _media_list_file(ctx):
    return f"{ctx.media_notes_dir}/_片单.md"


def create(params, state, ctx):
    """
    创建或切换影视笔记。

    params:
        name: str — 影视名称
        director: str — 导演（LLM 填写）
        media_type: str — 类型：电影/剧集/纪录片/动画（LLM 填写）
        year: str — 年份（LLM 填写）
        description: str — 简介（LLM 填写）
        thought: str — 可选，首条感想
    """
    name = (params.get("name") or "").strip()
    if not name:
        return {"success": False, "reply": "需要影视名称"}

    director = (params.get("director") or "未知").strip()
    media_type = (params.get("media_type") or "未知").strip()
    year = (params.get("year") or "未知").strip()
    description = (params.get("description") or "").strip()
    first_thought = (params.get("thought") or "").strip()

    file_path = _media_file(name, ctx)

    existing = OneDriveIO.read_text(file_path)
    if existing is None:
        return {"success": False, "reply": "OneDrive 读取失败"}

    if not existing.strip():
        template = f"""---
type: media
title: {name}
director: {director}
media_type: {media_type}
year: {year}
start_date: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}
status: watching
tags: [影视, {media_type}]
---

# 🎬 {name}

## 📋 基本信息

- **导演**：{director}
- **类型**：{media_type}
- **年份**：{year}
- **简介**：{description or '暂无'}
- **开始观看**：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}

---

## 💭 我的感想

---

## 🤖 AI 分析

---

## 🔗 关联推荐

---
"""
        if first_thought:
            template = template.replace(
                "## 💭 我的感想\n\n---",
                f"## 💭 我的感想\n\n{first_thought}\n*— {_now_str()}*\n\n---"
            )

        ok = OneDriveIO.write_text(file_path, template)
        if not ok:
            return {"success": False, "reply": "创建笔记失败"}

        _update_media_list(name, director, media_type, year, ctx)
        _log(f"[media.create] 新建: {name}")
    else:
        _log(f"[media.create] 切换到已有: {name}")
        # 文件已存在 + 有感想 → 自动转调 media.thought
        if first_thought:
            _log(f"[media.create] 已有笔记且携带感想，转调 media.thought")
            thought_result = thought({"content": first_thought, "media": name}, state, ctx)
            thought_result.setdefault("state_updates", {})["active_media"] = name
            return thought_result

    return {
        "success": True,
        "state_updates": {"active_media": name}
    }


def thought(params, state, ctx):
    """
    添加影视感想。

    params:
        content: str — 感想内容
        media: str — 可选，指定影视名称
    """
    content = (params.get("content") or "").strip()
    if not content:
        return {"success": False, "reply": "感想不能为空"}

    media = (params.get("media") or state.get("active_media", "")).strip()
    if not media:
        return {"success": False, "reply": "还没有在看的影视，先说一下名称吧"}

    entry = f"{content}\n*— {_now_str()}*\n"
    ok = OneDriveIO.append_to_section(_media_file(media, ctx), "## 💭 我的感想", entry)

    if ok:
        _log(f"[media.thought] 添加到 {media}")
        return {"success": True}
    else:
        return {"success": False, "reply": f"写入《{media}》失败"}


def _update_media_list(name, director, media_type, year, ctx):
    """更新片单索引"""
    existing = OneDriveIO.read_text(_media_list_file(ctx)) or ""
    if not existing.strip():
        existing = "# 🎬 片单\n\n| 名称 | 导演 | 类型 | 年份 | 状态 | 日期 |\n|------|------|------|------|------|------|\n"

    date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    new_row = f"| [[{name}]] | {director} | {media_type} | {year} | 👀 在看 | {date} |"
    new_content = existing.rstrip() + "\n" + new_row + "\n"
    OneDriveIO.write_text(_media_list_file(ctx), new_content)


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "media.create": create,
    "media.thought": thought,
}
