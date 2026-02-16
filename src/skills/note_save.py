# -*- coding: utf-8 -*-
"""
Skill: note.save
将用户消息保存到 Obsidian Quick-Notes。
支持纯文本和带附件的消息（图片/语音/视频/链接）。
"""
import sys
from storage import IO as OneDriveIO


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def execute(params, state, ctx):
    """
    保存笔记到 Quick-Notes.md

    params:
        content: str — 要保存的文本内容（LLM 可能会润色/总结）
        attachment: str — 可选，附件路径

    state:
        传入但本 skill 不修改 state

    ctx:
        UserContext — 用户上下文

    returns:
        {"success": bool, "reply": str}
    """
    content = (params.get("content") or "").strip()
    attachment = (params.get("attachment") or "").strip()

    if not content and not attachment:
        _log("[note.save] 无内容也无附件，跳过")
        return {"success": False, "reply": "没有可保存的内容"}

    # 构建要写入的 Markdown 文本
    message = _format_message(content, attachment)

    ok = OneDriveIO.append_to_quick_notes(ctx.quick_notes_file, message)

    if ok:
        _log(f"[note.save] 已保存: {message[:60]}...")
        return {"success": True}
    else:
        _log("[note.save] 保存失败")
        return {"success": False, "reply": "保存到 Obsidian 失败，稍后会重试"}


def _format_message(content, attachment):
    """
    根据内容和附件类型格式化为 Obsidian Markdown。
    附件路径来自 app.py 网关，已经上传到 OneDrive。
    """
    if not attachment:
        return content

    # 从完整 OneDrive 路径中提取 attachments/xxx.ext 的相对部分
    # 附件路径格式: /01_Obsidian/EmptyVault/00-Inbox/attachments/20260210_123456_img.jpg
    relative = attachment
    if "attachments/" in attachment:
        relative = "attachments/" + attachment.split("attachments/")[-1]

    # 根据文件扩展名判断类型
    ext = attachment.rsplit(".", 1)[-1].lower() if "." in attachment else ""

    if ext in ("jpg", "jpeg", "png", "gif", "webp"):
        # 图片：嵌入显示
        parts = [f"![[{relative}]]"]
        if content:
            parts.insert(0, content)
        return "\n\n".join(parts)

    elif ext in ("mp4", "mov", "avi", "webm"):
        # 视频：嵌入显示
        parts = [f"![[{relative}]]"]
        if content:
            parts.insert(0, content)
        return "\n\n".join(parts)

    elif ext in ("mp3", "wav", "amr", "silk", "m4a"):
        # 语音：显示 ASR 文本 + 原始录音链接
        parts = []
        if content:
            parts.append(f"> {content}")
        parts.append(f"🔗 原始录音: [[{relative}]]")
        return "\n\n".join(parts)

    else:
        # 其他文件
        parts = [f"📎 [[{relative}]]"]
        if content:
            parts.insert(0, content)
        return "\n\n".join(parts)


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "note.save": execute,
}
