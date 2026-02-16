# -*- coding: utf-8 -*-
"""
KarvisForAll 对话式设置
通过自然语言设置昵称、AI 风格、个人信息。
"""
import sys

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


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
        "reply": f"收到，我会{style}~",
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


# ============ Skill 注册 ============

SKILL_REGISTRY = {
    "settings.nickname": set_nickname,
    "settings.soul": set_soul,
    "settings.info": set_info,
}
