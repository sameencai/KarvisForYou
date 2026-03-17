# -*- coding: utf-8 -*-
"""
Skill: deep_dive (V3-F16)
主题深潜 — 对某个话题做跨时间线深度分析，直接回复用户（不写文件）。

工作方式：
1. 用户发送"帮我回顾一下 xxx"/"分析一下我和 xxx 的关系"/"最近情绪变化"
2. LLM 识别为 deep.dive，提取 topic + keywords
3. skill 搜索全历史数据（Quick-Notes + 归档笔记 + 情绪日记 + memory.md）
4. 汇总后调用 LLM 生成深度报告
5. 直接回复用户（默认不写文件）

支持的话题类型：
- 人际关系（"我和某人的事"）
- 情绪趋势（"最近的情绪"）
- 行为模式（"我在创造上花的时间"）
- 自由话题（"回顾一下工作"）
"""
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def dive(params, state, ctx):
    """
    deep.dive — 对指定话题做跨时间线深度分析。

    params:
        topic: str — 话题描述（如"和某人的关系"、"最近的情绪"）
        keywords: list[str] — 搜索关键词（如["朋友", "聚会"]）
        save: bool — 是否保存报告到文件（默认 false）
    """
    topic = params.get("topic", "")
    keywords = params.get("keywords", [])
    save = params.get("save", True)  # 默认保存报告到文件

    if not topic:
        return {"success": False, "reply": "想深潜什么话题呢？告诉我你想回顾的内容~"}

    if not keywords:
        # 从 topic 自动提取关键词
        keywords = [w.strip() for w in topic.replace("的", " ").replace("和", " ").split() if len(w.strip()) >= 2]
        if not keywords:
            keywords = [topic]

    _log(f"[deep_dive] 开始深潜: topic={topic}, keywords={keywords}")

    # 1. 搜索全历史数据
    raw_data = _collect_data(keywords, state, ctx)
    if not raw_data.get("has_data"):
        return {"success": True, "reply": f"翻了翻记录，关于「{topic}」的数据还不太多，等积累更多记录再来分析吧~"}

    # 2. 调用 LLM 生成深度报告
    report = _generate_report(topic, keywords, raw_data, state)
    if not report:
        return {"success": True, "reply": "分析生成失败了，稍后再试试~"}

    # 3. 可选保存
    if save:
        _save_report(topic, report, ctx)

    return {"success": True, "reply": report}


def _collect_data(keywords, state, ctx):
    """搜索全历史数据，返回结构化结果"""
    # 获取线程池
    try:
        from brain import _executor
        executor = _executor
    except Exception:
        executor = ThreadPoolExecutor(max_workers=6)

    # 需要搜索的全量文件
    files_to_read = {
        "quick_notes": ctx.quick_notes_file,
        "misc": ctx.misc_file,
        "decisions": ctx.decision_log_file,
    }

    # 也搜索最近 30 天的归档笔记
    today = datetime.now(BEIJING_TZ).date()
    for i in range(30):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        files_to_read[f"emotion_{d}"] = f"{ctx.emotion_notes_dir}/{d}.md"
        files_to_read[f"work_{d}"] = f"{ctx.work_notes_dir}/{d}.md"
        files_to_read[f"fun_{d}"] = f"{ctx.fun_notes_dir}/{d}.md"

    # 也读取 memory.md
    files_to_read["memory"] = ctx.memory_file

    # 并发读取
    futures = {k: executor.submit(ctx.IO.read_text, v) for k, v in files_to_read.items()}

    results = {}
    for k, fut in futures.items():
        try:
            results[k] = fut.result(timeout=30) or ""
        except Exception:
            results[k] = ""

    # 从各文件中提取匹配关键词的片段
    matched_entries = []
    keyword_lower = [kw.lower() for kw in keywords]

    # Quick-Notes：按日期+时间分段搜索
    qn_text = results.get("quick_notes", "")
    if qn_text:
        qn_entries = _search_in_quick_notes(qn_text, keyword_lower)
        matched_entries.extend(qn_entries)

    # 碎碎念
    misc_text = results.get("misc", "")
    if misc_text:
        misc_entries = _search_in_text(misc_text, keyword_lower, "碎碎念")
        matched_entries.extend(misc_entries)

    # 归档笔记
    for k, v in results.items():
        if not v:
            continue
        if k.startswith("emotion_"):
            date = k.replace("emotion_", "")
            entries = _search_in_text(v, keyword_lower, f"情感日记({date})", date)
            matched_entries.extend(entries)
        elif k.startswith("work_"):
            date = k.replace("work_", "")
            entries = _search_in_text(v, keyword_lower, f"工作笔记({date})", date)
            matched_entries.extend(entries)
        elif k.startswith("fun_"):
            date = k.replace("fun_", "")
            entries = _search_in_text(v, keyword_lower, f"生活趣事({date})", date)
            matched_entries.extend(entries)

    # Memory.md
    memory_text = results.get("memory", "")
    memory_relevant = ""
    if memory_text:
        for kw in keyword_lower:
            if kw in memory_text.lower():
                # 提取包含关键词的段落
                for para in memory_text.split("\n\n"):
                    if kw in para.lower():
                        memory_relevant += para + "\n\n"

    # 决策日志
    decisions_text = results.get("decisions", "")
    decision_entries = []
    if decisions_text:
        for line in decisions_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            line_lower = line.lower()
            if any(kw in line_lower for kw in keyword_lower):
                decision_entries.append(line)

    # 情绪评分趋势
    mood_scores = state.get("mood_scores", [])

    has_data = bool(matched_entries) or bool(memory_relevant) or bool(decision_entries)

    # 按日期降序排序，确保最新条目优先送入 LLM
    def _sort_key(e):
        d = e.get("date", "") or ""
        t = e.get("time", "") or ""
        return f"{d} {t}"
    matched_entries.sort(key=_sort_key, reverse=True)

    return {
        "has_data": has_data,
        "matched_entries": matched_entries[:50],  # 最多 50 条，最新优先
        "memory_relevant": memory_relevant[:1000],
        "decision_entries": decision_entries[-10:],
        "mood_scores": mood_scores[-30:],  # 最近 30 天评分
        "total_matches": len(matched_entries),
    }


def _search_in_quick_notes(text, keywords):
    """从 Quick-Notes 中搜索包含关键词的条目"""
    entries = []
    sections = text.split("\n## ")
    for section in sections[1:]:
        lines = section.split("\n")
        date_line = lines[0].strip() if lines else ""
        # 按 ### HH:MM 分割为消息条目
        sub_entries = section.split("\n### ")
        for sub in sub_entries[1:]:
            sub_lower = sub.lower()
            if any(kw in sub_lower for kw in keywords):
                time_line = sub.split("\n")[0].strip()
                body = "\n".join(sub.split("\n")[1:]).strip()[:200]
                entries.append({
                    "source": "Quick-Notes",
                    "date": date_line[:10],
                    "time": time_line[:5],
                    "content": body
                })
    return entries


def _search_in_text(text, keywords, source, date=""):
    """从普通文本中搜索包含关键词的段落"""
    entries = []
    # 按段落分割
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        para_lower = para.lower()
        if any(kw in para_lower for kw in keywords):
            entries.append({
                "source": source,
                "date": date,
                "time": "",
                "content": para.strip()[:200]
            })
    return entries


def _generate_report(topic, keywords, raw_data, state):
    """调用 LLM 生成深度分析报告"""
    from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    # 组装数据摘要
    entries_text = ""
    for e in raw_data["matched_entries"]:
        date_time = f"{e['date']} {e['time']}" if e.get('date') else ""
        entries_text += f"[{e['source']}] {date_time} {e['content']}\n\n"

    memory_text = raw_data.get("memory_relevant", "")
    mood_text = ""
    mood_scores = raw_data.get("mood_scores", [])
    if mood_scores:
        recent = mood_scores[-14:]  # 最近 14 天
        mood_text = " / ".join(
            f"{s.get('date', '')}:{s.get('score', '?')}"
            for s in recent
        )

    decision_text = "\n".join(raw_data.get("decision_entries", []))

    import prompts
    prompt = prompts.get(
        "DEEP_DIVE_USER",
        topic=topic,
        total_matches=raw_data['total_matches'],
        shown_count=len(raw_data['matched_entries']),
        entries_text=entries_text[:3000],
        memory_text=memory_text[:500] if memory_text else "无",
        mood_text=mood_text if mood_text else "无数据",
        decision_text=decision_text[:500] if decision_text else "无",
    )

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": prompts.DEEP_DIVE_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 800,
        "temperature": 0.4
    }

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        if resp.status_code == 200:
            report = resp.json()["choices"][0]["message"]["content"].strip()
            _log(f"[deep_dive] 报告生成完成: {len(report)} chars")
            return report
        _log(f"[deep_dive] LLM 失败: {resp.status_code}")
    except Exception as e:
        _log(f"[deep_dive] 分析异常: {e}")
    return None


def _save_report(topic, report, ctx):
    """将报告保存到文件"""
    now = datetime.now(BEIJING_TZ)
    date_str = now.strftime("%Y-%m-%d")
    # 简化 topic 为文件名
    safe_topic = topic.replace("/", "-").replace("\\", "-").replace(" ", "")[:20]
    file_path = f"{ctx.base_dir}/02-Notes/深潜报告/{date_str}-{safe_topic}.md"

    content = f"""---
date: {date_str}
type: deep-dive
topic: {topic}
tags: [deep-dive]
---

{report}
"""
    try:
        ok = ctx.IO.write_text(file_path, content)
        if ok:
            _log(f"[deep_dive] 报告已保存: {file_path}")
        else:
            _log(f"[deep_dive] 报告保存失败: {file_path}")
    except Exception as e:
        _log(f"[deep_dive] 保存异常: {e}")


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "deep.dive": dive,
}
