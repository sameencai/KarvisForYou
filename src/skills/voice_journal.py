# -*- coding: utf-8 -*-
"""
Skill: voice_journal (V3-F14)
语音日记 — 长语音(>30s / >200字) 自动整理成结构化日记。

工作方式：
1. brain.py 检测到语音消息 ASR 文本 > 200 字时，LLM 选择 voice.journal
2. skill 调用 LLM 做二次分析：提取主题/关键事件/情绪/人物 → 生成结构化日记
3. 写入 02-Notes/语音日记/{date}-{序号}.md
4. 回复用户："帮你把刚才的语音整理成了一篇日记~"

输出文件结构：
---
date: 2026-02-11
type: voice-journal
tags: [voice-journal]
---
# 🎙️ 语音日记 · 2026-02-11 下午

## 你说了什么
（整理后的 ASR 文本，分段，去口语化重复）

## Karvis 的整理
**主题**：xxx
**情绪轨迹**：xxx → xxx → xxx
**关键洞察**：xxx
"""
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def journal(params, state, ctx):
    """
    voice.journal — 将长语音 ASR 文本整理为结构化日记。

    params:
        asr_text: str — ASR 识别的原始文本
        attachment: str — 语音文件 OneDrive 路径（可选）
        duration_hint: str — 时长提示（如"2分30秒"，可选）
    """
    asr_text = params.get("asr_text", "")
    attachment = params.get("attachment", "")
    duration_hint = params.get("duration_hint", "")

    if not asr_text or len(asr_text) < 100:
        return {"success": True, "reply": "语音内容太短，就不单独整理了~ 已保存到笔记里"}

    now = datetime.now(BEIJING_TZ)
    date_str = now.strftime("%Y-%m-%d")
    time_period = "上午" if now.hour < 12 else ("下午" if now.hour < 18 else "晚上")

    # 调用 LLM 做语音日记分析
    analysis = _analyze_voice(asr_text, state)
    if not analysis:
        return {"success": True, "reply": "语音日记整理失败了，不过内容已保存到笔记里"}

    # 构建日记内容
    content = _build_journal_content(
        date_str, time_period, asr_text, analysis,
        attachment, duration_hint
    )

    # 写入文件
    file_path = _write_journal_file(date_str, content, ctx)
    if not file_path:
        return {"success": True, "reply": "日记写入失败了，不过语音已保存"}

    # 构建回复
    theme = analysis.get("theme", "")
    mood = analysis.get("mood_trajectory", "")
    reply_parts = ["🎙️ 帮你把刚才的语音整理成了一篇日记~"]
    if theme:
        reply_parts.append(f"主题：{theme}")
    if mood:
        reply_parts.append(f"情绪轨迹：{mood}")
    insight = analysis.get("insight", "")
    if insight:
        reply_parts.append(f"\n💡 {insight}")

    return {"success": True, "reply": "\n".join(reply_parts)}


def _analyze_voice(asr_text, state):
    """调用 DeepSeek 分析语音文本，提取结构化信息"""
    from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    # 从 state 获取上下文
    active_book = state.get("active_book", "")
    active_media = state.get("active_media", "")

    context_hints = []
    if active_book:
        context_hints.append(f"用户正在读《{active_book}》")
    if active_media:
        context_hints.append(f"用户正在看《{active_media}》")

    context_str = "；".join(context_hints) if context_hints else "无特殊上下文"

    import prompts
    prompt = prompts.get("VOICE_USER", asr_text=asr_text, context_str=context_str)

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": prompts.VOICE_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 800,
        "temperature": 0.3
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            # 解析 JSON
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                result = json.loads(text[start:end + 1])
                _log(f"[voice_journal] 分析完成: theme={result.get('theme', '')}")
                return result
        _log(f"[voice_journal] LLM 分析失败: {resp.status_code}")
    except Exception as e:
        _log(f"[voice_journal] 分析异常: {e}")
    return None


def _build_journal_content(date_str, time_period, asr_text, analysis, attachment, duration_hint):
    """构建语音日记 Markdown 内容"""
    cleaned = analysis.get("cleaned_text", asr_text)
    theme = analysis.get("theme", "语音记录")
    mood = analysis.get("mood_trajectory", "")
    events = analysis.get("key_events", [])
    people = analysis.get("people_mentioned", [])
    insight = analysis.get("insight", "")

    duration_str = f"（{duration_hint}）" if duration_hint else ""

    parts = [
        "---",
        f"date: {date_str}",
        "type: voice-journal",
        "tags: [voice-journal]",
        "---",
        "",
        f"# 🎙️ 语音日记 · {date_str} {time_period}",
        "",
        "## 你说了什么",
        "",
        cleaned,
        "",
    ]

    # Karvis 的整理
    parts.append("## Karvis 的整理")
    parts.append("")
    parts.append(f"**主题**：{theme}")
    if mood:
        parts.append(f"**情绪轨迹**：{mood}")
    if events:
        parts.append(f"**关键事件**：{'、'.join(events)}")
    if people:
        parts.append(f"**提到的人**：{'、'.join(people)}")
    if insight:
        parts.append(f"**关键洞察**：{insight}")

    parts.append("")
    parts.append("---")
    parts.append("")

    footer_parts = ["*🤖 基于语音自动整理"]
    if duration_hint:
        footer_parts[0] = f"*🤖 基于 {duration_hint} 语音自动整理"
    if attachment:
        footer_parts.append(f"原始录音：`{attachment}`")
    parts.append(" | ".join(footer_parts) + "*")

    return "\n".join(parts)


def _write_journal_file(date_str, content, ctx):
    """写入语音日记文件，自动处理序号"""
    from storage import IO as OneDriveIO

    base_dir = ctx.voice_journal_dir

    # 检查是否已有当天的语音日记（最多检查 5 个序号）
    for seq in range(1, 6):
        suffix = f"-{seq}" if seq > 1 else ""
        file_path = f"{base_dir}/{date_str}{suffix}.md"
        existing = OneDriveIO.read_text(file_path)
        if existing is None or existing == "":
            # 文件不存在或为空，写入
            ok = OneDriveIO.write_text(file_path, content)
            if ok:
                _log(f"[voice_journal] 写入: {file_path}")
                return file_path
            else:
                _log(f"[voice_journal] 写入失败: {file_path}")
                return None

    _log(f"[voice_journal] 当天已有 5 篇语音日记，跳过")
    return None


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "voice.journal": journal,
}
