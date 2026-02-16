# -*- coding: utf-8 -*-
"""
Skill: daily.generate
读取当天的 Quick-Notes 和归档笔记，调用 DeepSeek 生成日报，写入 Daily Note。
"""
import sys
import re
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def execute(params, state, ctx):
    """
    生成今日日报。

    params:
        date: str — 可选，指定日期 YYYY-MM-DD，默认今天
    """
    date_str = (params.get("date") or "").strip()
    if not date_str:
        date_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    _log(f"[daily.generate] 开始生成 {date_str} 日报")

    # 1. 收集当天所有内容
    notes = _collect_today_notes(date_str, ctx)

    if not notes.strip():
        _log("[daily.generate] 今天没有笔记内容")
        return {"success": True, "reply": f"今天（{date_str}）还没有记录，无法生成日报"}

    # 2. 调用 AI 分析
    from brain import call_deepseek
    analysis = _ai_analyze(notes, date_str, call_deepseek)

    if not analysis:
        return {"success": False, "reply": "AI 分析失败，日报生成中止"}

    # 3. 构建日报 Markdown
    daily_md = _build_daily_report(date_str, analysis, notes)

    # 4. 写入 Daily Note（合并，不覆盖打卡内容）
    file_path = f"{ctx.daily_notes_dir}/{date_str}.md"
    ok = _write_daily_note(file_path, date_str, daily_md)

    if ok:
        _log(f"[daily.generate] 日报已写入: {file_path}")
        mood = analysis.get("mood", "")
        summary = analysis.get("summary", "")[:60]
        return {"success": True, "reply": f"日报已生成 {mood}\n{summary}"}
    else:
        return {"success": False, "reply": "日报写入失败"}


def _collect_today_notes(date_str, ctx):
    """收集当天所有笔记内容（并发读取所有文件）"""
    from concurrent.futures import ThreadPoolExecutor

    # 并发读取所有可能的文件
    files_to_read = {
        "quick_notes": ctx.quick_notes_file,
        "work": f"{ctx.work_notes_dir}/{date_str}.md",
        "emotion": f"{ctx.emotion_notes_dir}/{date_str}.md",
        "fun": f"{ctx.fun_notes_dir}/{date_str}.md",
        "misc": ctx.misc_file,
    }

    results = {}
    # 复用 brain 的全局线程池，避免重复创建
    try:
        from brain import _executor
        futures = {key: _executor.submit(OneDriveIO.read_text, path) for key, path in files_to_read.items()}
    except ImportError:
        # fallback: 创建临时线程池
        _pool = ThreadPoolExecutor(max_workers=5)
        futures = {key: _pool.submit(OneDriveIO.read_text, path) for key, path in files_to_read.items()}

    for key, fut in futures.items():
        try:
            results[key] = fut.result(timeout=30) or ""
        except Exception:
            results[key] = ""

    parts = []

    # Quick-Notes
    today_entries = _extract_date_entries(results["quick_notes"], date_str)
    if today_entries:
        parts.extend(["【快速笔记】", today_entries])

    # 分类归档
    for key, label in [("work", "工作笔记"), ("emotion", "情感日记"), ("fun", "生活趣事")]:
        content = results[key].strip()
        if content:
            parts.extend([f"【{label}】", content])

    # 碎碎念
    misc_entries = _extract_date_entries(results["misc"], date_str)
    if misc_entries:
        parts.extend(["【碎碎念】", misc_entries])

    return "\n\n".join(parts)


def _extract_date_entries(text, date_str):
    """从 Markdown 文件中提取指定日期的条目"""
    entries = []
    sections = text.split("\n## ")
    for section in sections[1:]:
        # 检查 section 的时间戳是否匹配日期
        first_line = section.split("\n")[0].strip()
        if first_line.startswith(date_str):
            entries.append("## " + section.strip())
    return "\n\n".join(entries)


def _ai_analyze(notes, date_str, call_deepseek):
    """调用 AI 分析当天笔记"""
    import json
    import prompts

    response = call_deepseek([
        {"role": "system", "content": prompts.DAILY_SYSTEM},
        {"role": "user", "content": prompts.get("DAILY_USER", date_str=date_str, notes=notes[:3000])}
    ], max_tokens=800, temperature=0.7)

    if not response:
        return None

    # 解析 JSON
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    _log(f"[daily.generate] AI 分析 JSON 解析失败: {text[:200]}")
    return None


def _build_daily_report(date_str, analysis, notes):
    """构建日报 Markdown"""
    mood = analysis.get("mood", "📝")
    summary = analysis.get("summary", "")
    tags = analysis.get("tags", [])
    highlights = analysis.get("highlights", [])
    insights = analysis.get("insights", "")
    mood_score = analysis.get("mood_score", "")

    tag_str = " ".join([f"#{t}" for t in tags]) if tags else ""

    lines = [
        f"## 📊 今日总结",
        "",
        f"{mood} {summary}",
        "",
    ]

    if mood_score:
        lines.extend([f"**状态评分**: {mood_score}/10", ""])

    if tag_str:
        lines.extend([f"**标签**: {tag_str}", ""])

    if highlights:
        lines.append("**今日亮点**:")
        for h in highlights:
            lines.append(f"- {h}")
        lines.append("")

    if insights:
        lines.extend([f"**洞察**: {insights}", ""])

    lines.extend([
        "---",
        "",
        "## 📝 原始记录",
        "",
    ])

    # 附上原始笔记的简要版本（截取，避免文件过大）
    if len(notes) > 2000:
        lines.append(notes[:2000] + "\n\n...(更多内容见各分类笔记)")
    else:
        lines.append(notes)

    lines.extend(["", f"*🤖 AI 生成于 {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')}*", ""])

    return "\n".join(lines)


def _write_daily_note(file_path, date_str, daily_content):
    """写入 Daily Note，与打卡内容合并"""
    existing = OneDriveIO.read_text(file_path)
    if existing is None:
        existing = ""

    if not existing.strip():
        # 全新 Daily Note
        new_content = f"# {date_str}\n\n{daily_content}"
    elif "## 📊 今日总结" in existing:
        # 替换已有的日报部分
        parts = existing.split("## 📊 今日总结")
        before = parts[0]
        # 找到日报结束位置（下一个一级 section 或 ## 每日复盘）
        after_text = parts[1]
        # 找 "## 每日复盘" 或文件末尾
        checkin_idx = after_text.find("## 每日复盘")
        if checkin_idx >= 0:
            after = after_text[checkin_idx:]
            new_content = before + daily_content + "\n\n" + after
        else:
            new_content = before + daily_content
    else:
        # 追加到已有内容之前（日报在打卡之前）
        if "## 每日复盘" in existing:
            parts = existing.split("## 每日复盘")
            new_content = parts[0].rstrip() + "\n\n" + daily_content + "\n\n## 每日复盘" + parts[1]
        else:
            new_content = existing.rstrip() + "\n\n" + daily_content

    return OneDriveIO.write_text(file_path, new_content)


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "daily.generate": execute,
}
