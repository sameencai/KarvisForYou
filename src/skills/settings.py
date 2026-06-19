# -*- coding: utf-8 -*-
"""
KarvisForAll 对话式设置
通过自然语言设置昵称、AI 风格、个人信息。
"""
import sys

def _log(msg):
    from logger import log
    log(msg)


def set_nickname(params, state, ctx):
    """
    设置昵称。
    LLM 提取 nickname 后调用此 handler。
    写入：user_config.json + 注册表 + memory_updates（由 brain 处理）
    """
    nickname = params.get("nickname", "").strip()
    if not nickname:
        return {"success": False, "reply": "没有识别到昵称，你可以说「叫我小明」来设置~"}

    # 写入 user_config.json
    config = ctx.get_user_config()
    config["nickname"] = nickname
    ctx.save_user_config(config)

    # 同步更新注册表
    from user_context import update_user_nickname
    update_user_nickname(ctx.user_id, nickname)

    _log(f"[Settings] 用户 {ctx.user_id} 设置昵称: {nickname}")

    return {
        "success": True,
        "reply": f"好的，以后叫你「{nickname}」啦~",
        "memory_updates": [
            {
                "section": "关键偏好",
                "action": "upsert",
                "content": f"用户希望被称为「{nickname}」"
            }
        ]
    }


def set_ai_name(params, state, ctx):
    """
    给 AI 起昵称（用户说"我叫你XX"、"你叫XX"、"以后叫你XX"时触发）。
    区别于 set_nickname（设置用户自己的昵称）。
    """
    ai_name = params.get("ai_name", "").strip()
    if not ai_name:
        return {"success": False, "reply": "没听清你想叫我什么，再说一次？"}

    config = ctx.get_user_config()
    config["ai_name"] = ai_name
    ctx.save_user_config(config)

    _log(f"[Settings] 用户 {ctx.user_id} 给AI起名: {ai_name}")

    return {
        "success": True,
        "reply": f"好呀，以后我就是「{ai_name}」啦~",
        "memory_updates": [
            {
                "section": "关键偏好",
                "action": "upsert",
                "content": f"用户给 Karvis 起了昵称「{ai_name}」，喜欢被叫这个名字"
            }
        ]
    }


def set_soul(params, state, ctx):
    """
    设置 AI 人格风格覆写。
    用户说"说话活泼一点"、"正式一些"、"恢复默认风格"等。
    """
    style = params.get("style", "").strip()
    mode = params.get("mode", "set")  # set / append / reset

    config = ctx.get_user_config()

    if mode == "reset":
        config["soul_override"] = ""
        ctx.save_user_config(config)
        _log(f"[Settings] 用户 {ctx.user_id} 重置风格")
        return {
            "success": True,
            "reply": "已恢复默认风格~"
        }

    if not style:
        return {"success": False, "reply": "没有识别到风格描述，试试说「说话活泼一点」？"}

    current = config.get("soul_override", "")

    if mode == "append" and current:
        # 累加模式：在原有基础上追加
        new_style = f"{current}；{style}"
    else:
        new_style = style

    config["soul_override"] = new_style
    ctx.save_user_config(config)

    _log(f"[Settings] 用户 {ctx.user_id} 设置风格: {new_style}")

    return {
        "success": True,
        "prefer_llm_reply": True,  # 优先用 LLM 预生成的回复（更自然），仅在 LLM 没给 reply 时才用兜底
        "reply": f"风格已更新~",
        "memory_updates": [
            {
                "section": "关键偏好",
                "action": "upsert",
                "content": f"用户偏好的交互风格：{new_style}"
            }
        ]
    }


def set_info(params, state, ctx):
    """
    设置个人信息（职业、城市、宠物等）。
    这些信息主要通过 memory_updates 写入 memory.md，
    brain 会自动处理 memory_updates。
    """
    info_text = params.get("info", "").strip()
    category = params.get("category", "")  # occupation / city / pets / other

    if not info_text:
        return {"success": False, "reply": "没有识别到个人信息哦~"}

    # 同时写入 user_config.json 的 info 字段（结构化存储）
    config = ctx.get_user_config()
    if "info" not in config:
        config["info"] = {}

    if category and category != "other":
        config["info"][category] = info_text

    ctx.save_user_config(config)

    _log(f"[Settings] 用户 {ctx.user_id} 设置信息: {category}={info_text}")

    # memory_updates 由 brain 层统一处理写入 memory.md
    return {
        "success": True,
        "reply": f"记住了~",
        "memory_updates": [
            {
                "section": "重要的人" if category == "people" else "关键偏好",
                "action": "upsert",
                "content": info_text
            }
        ]
    }

def manage_skills(params, state, ctx):
    """
    V12: 对话式 Skill 管理。
    action: list / enable / disable
    skill_names: 要操作的 skill 名列表（enable/disable 时需要）
    """
    action = params.get("action", "list")

    if action == "list":
        return _list_skills(ctx)
    elif action == "disable":
        return _toggle_skills(ctx, params.get("skill_names", []), disable=True)
    elif action == "enable":
        return _toggle_skills(ctx, params.get("skill_names", []), disable=False)
    else:
        return {"success": False, "reply": "我不太理解你要做什么，试试说「我有什么功能」？"}


def _list_skills(ctx):
    """列出用户的所有可见 Skill，分三组：已开启 / 未开启 / 敬请期待"""
    from skill_loader import load_skill_registry, get_skill_metadata

    load_skill_registry()
    metadata = get_skill_metadata()

    enabled = []
    disabled = []
    preview = []

    # Skill 名到友好名的映射（按前缀分组）
    _DISPLAY_NAMES = {
        "note": "📝 笔记",
        "todo": "✅ 待办",
        "checkin": "📊 打卡",
        "classify": "📂 分类归档",
        "daily": "📅 日报",
        "book": "📖 读书笔记",
        "media": "🎬 影视笔记",
        "mood": "😊 情绪日记",
        "weekly": "📅 周回顾",
        "monthly": "📊 月度回顾",
        "habit": "🔬 微习惯",
        "decision": "🎯 决策追踪",
        "deep": "🔍 主题深潜",
        "reflect": "💭 深度自问",
        "voice": "🎙️ 语音日记",
        "settings": "⚙️ 设置",
        "web": "🔗 数据查看",
        "dynamic": "🔧 动态操作",
        "internal": "📁 文件查阅",
    }
    _seen_prefixes = set()

    for name, meta in sorted(metadata.items()):
        vis = meta.get("visibility", "public")

        # private → 完全不显示
        if vis == "private" and not ctx.is_admin:
            continue

        # preview → 显示"敬请期待"
        if vis == "preview" and not ctx.is_admin:
            prefix = name.split(".")[0]
            if prefix not in _seen_prefixes:
                _seen_prefixes.add(prefix)
                display = _DISPLAY_NAMES.get(prefix, prefix)
                preview.append(display)
            continue

        # public / admin 可见的 private
        prefix = name.split(".")[0]
        if prefix in _seen_prefixes:
            continue
        _seen_prefixes.add(prefix)

        display = _DISPLAY_NAMES.get(prefix, prefix)
        if ctx.is_skill_allowed(name):
            enabled.append(display)
        else:
            disabled.append(display)

    # 组装回复
    lines = []
    if enabled:
        lines.append("你目前开启了以下功能：\n")
        lines.append("\n".join(f"  {s}" for s in enabled))

    if disabled:
        lines.append("\n\n未开启：")
        lines.append("\n".join(f"  {s}" for s in disabled))

    if preview:
        lines.append("\n\n即将上线：")
        lines.append("\n".join(f"  {s} — 敬请期待~" for s in preview))

    lines.append("\n\n想开启或关闭某个功能，直接告诉我就好~")

    return {
        "success": True,
        "reply": "\n".join(lines),
    }


def _toggle_skills(ctx, skill_names, disable=True):
    """开启或关闭指定 Skill"""
    if not skill_names:
        action_word = "关闭" if disable else "开启"
        return {"success": False, "reply": f"没听清你要{action_word}哪个功能，再说一次？"}

    # 检查 visibility — private 不透露存在，preview 提示敬请期待
    from skill_loader import load_skill_registry, get_skill_metadata
    load_skill_registry()
    metadata = get_skill_metadata()

    for name in skill_names:
        # 查找精确匹配或通配符匹配的第一个 skill
        matched_meta = metadata.get(name, {})
        if not matched_meta:
            # 尝试 prefix 匹配
            for k, v in metadata.items():
                if k.startswith(name.rstrip("*").rstrip(".")):
                    matched_meta = v
                    break
        vis = matched_meta.get("visibility", "public")
        if vis == "private" and not ctx.is_admin:
            return {"success": False, "reply": "我目前没有这个功能哦~ 如果你想管理待办或记笔记，随时告诉我~"}
        if vis == "preview" and not ctx.is_admin:
            return {"success": False, "reply": "该功能即将在订阅版上线，敬请期待~ 目前你可以用文字描述给我，我一样能帮到你~"}

    config = ctx.get_user_config()
    skills_cfg = config.get("skills", {"mode": "blacklist", "list": []})
    mode = skills_cfg.get("mode", "blacklist")
    skill_list = skills_cfg.get("list", [])

    action_word = "关闭" if disable else "开启"
    changed = []

    for name in skill_names:
        if mode == "blacklist":
            if disable:
                if name not in skill_list:
                    skill_list.append(name)
                    changed.append(name)
            else:  # enable → remove from blacklist
                if name in skill_list:
                    skill_list.remove(name)
                    changed.append(name)
        else:  # whitelist
            if disable:
                if name in skill_list:
                    skill_list.remove(name)
                    changed.append(name)
            else:  # enable → add to whitelist
                if name not in skill_list:
                    skill_list.append(name)
                    changed.append(name)

    skills_cfg["list"] = skill_list
    config["skills"] = skills_cfg
    ctx.save_user_config(config)

    # 更新内存缓存
    ctx._skills_config = skills_cfg

    if changed:
        friendly = "、".join(f"「{n.split('.')[0]}」" for n in changed)
        return {"success": True, "reply": f"好的，已{action_word}{friendly}~ 需要的时候随时跟我说~"}
    else:
        return {"success": True, "reply": f"这些功能已经是{action_word}状态啦~"}


# ============ Skill 注册 ============

SKILL_REGISTRY = {
    "settings.nickname": set_nickname,
    "settings.ai_name": set_ai_name,
    "settings.soul": set_soul,
    "settings.info": set_info,
    "settings.skills": manage_skills,
}
