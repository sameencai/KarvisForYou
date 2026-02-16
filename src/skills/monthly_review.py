# -*- coding: utf-8 -*-
"""
Skill: monthly.review
每月末自动生成月度成长回顾：情绪曲线、人际变化、高光低谷、成长洞察。
写入 01-Daily/月报-{YYYY-MM}.md

数据源：
1. Quick-Notes（整月条目）
2. 归档笔记（emotion/fun/work 各天 + 碎碎念）
3. Daily Note（日报 + 打卡）
4. 情绪日记（mood_score + key_moments）
5. 周报（已生成的周报文件）
6. 决策日志（skill 使用统计）
7. state.mood_scores（情绪评分数组）
8. state.checkin_stats（打卡统计）
"""
import sys
import json
import calendar
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def execute(params, state, ctx):
    """
    生成月度回顾。

    params:
        month: str — 可选，YYYY-MM 格式，默认当前月
    """
    month_str = (params.get("month") or "").strip()
    if not month_str:
        month_str = datetime.now(BEIJING_TZ).strftime("%Y-%m")

    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except (ValueError, IndexError):
        return {"success": False, "reply": f"月份格式错误：{month_str}"}

    _, days_in_month = calendar.monthrange(year, month)
    dates = [f"{year}-{month:02d}-{d:02d}" for d in range(1, days_in_month + 1)]
    period_str = f"{year}年{month}月"

    _log(f"[monthly.review] 生成月报: {period_str} ({days_in_month}天)")

    # 1. 并发收集整月数据
    data = _collect_month_data(dates, month_str, state, ctx)

    if not data["notes"].strip():
        _log("[monthly.review] 本月没有记录")
        return {"success": True, "reply": f"本月（{period_str}）没有记录，无法生成月报"}

    # 2. AI 分析
    from brain import call_deepseek
    analysis = _ai_analyze_month(data, period_str, month_str, dates, call_deepseek)

    if not analysis:
        return {"success": False, "reply": "AI 月报分析失败"}

    # 3. 构建 Markdown
    review_md = _build_monthly_review(period_str, month_str, analysis, data)

    # 4. 写入文件
    file_path = f"{ctx.daily_notes_dir}/月报-{month_str}.md"
    ok = OneDriveIO.write_text(file_path, review_md)

    if ok:
        _log(f"[monthly.review] 月报已写入: {file_path}")
        mood_avg = analysis.get("mood_avg", "?")
        insight = analysis.get("insight", "")[:60]
        return {
            "success": True,
            "reply": f"📊 月度回顾已生成（{period_str}）\n情绪均分：{mood_avg}/10\n{insight}"
        }
    else:
        return {"success": False, "reply": "月报写入失败"}


def _collect_month_data(dates, month_str, state, ctx):
    """并发收集整月数据"""
    from concurrent.futures import ThreadPoolExecutor

    # 构建要读取的文件列表
    files_to_read = {
        "quick_notes": ctx.quick_notes_file,
        "misc": ctx.misc_file,
        "decisions": ctx.decision_log_file,
    }

    # 读取每日文件（Daily Note、情绪日记、归档）
    for d in dates:
        files_to_read[f"daily_{d}"] = f"{ctx.daily_notes_dir}/{d}.md"
        files_to_read[f"emotion_{d}"] = f"{ctx.emotion_notes_dir}/{d}.md"
        files_to_read[f"fun_{d}"] = f"{ctx.fun_notes_dir}/{d}.md"
        files_to_read[f"work_{d}"] = f"{ctx.work_notes_dir}/{d}.md"

    # 读取周报
    # 找出本月所有可能的周报日期（周一开始的日期）
    week_dates = set()
    for d in dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            # 周报文件名格式：周报-{周一日期}.md
            monday = dt - timedelta(days=dt.weekday())
            week_dates.add(monday.strftime("%Y-%m-%d"))
        except Exception:
            pass
    for wd in week_dates:
        files_to_read[f"weekly_{wd}"] = f"{ctx.daily_notes_dir}/周报-{wd}.md"

    # 并发读取
    results = {}
    try:
        from brain import _executor
        executor = _executor
    except ImportError:
        executor = ThreadPoolExecutor(max_workers=6)

    futures = {k: executor.submit(OneDriveIO.read_text, v) for k, v in files_to_read.items()}

    for k, fut in futures.items():
        try:
            results[k] = fut.result(timeout=30) or ""
        except Exception:
            results[k] = ""

    # 组装各天数据（只取有内容的天，做摘要）
    all_parts = []
    record_days = 0

    for d in dates:
        day_parts = []

        # Quick-Notes 该日条目
        qn_entries = _extract_date_entries(results["quick_notes"], d)
        if qn_entries:
            day_parts.append(qn_entries[:300])

        # 归档笔记
        for key in ("emotion", "fun", "work"):
            content = results.get(f"{key}_{d}", "").strip()
            if content:
                day_parts.append(content[:200])

        # 碎碎念该日条目
        misc_entries = _extract_date_entries(results["misc"], d)
        if misc_entries:
            day_parts.append(misc_entries[:150])

        # Daily Note 摘要
        daily = results.get(f"daily_{d}", "").strip()
        if daily and "## 📊 今日总结" in daily:
            summary_section = daily.split("## 📊 今日总结")[1]
            end_idx = summary_section.find("\n## ")
            if end_idx >= 0:
                summary_section = summary_section[:end_idx]
            day_parts.append(f"[日报] {summary_section.strip()[:200]}")

        if day_parts:
            all_parts.append(f"=== {d} ===\n" + "\n".join(day_parts))
            record_days += 1

    # 截断整月笔记到合理长度
    notes = "\n\n".join(all_parts)
    if len(notes) > 8000:
        notes = notes[:8000] + "\n\n... (更多记录已截断)"

    # 收集周报摘要
    weekly_summaries = []
    for wd in sorted(week_dates):
        weekly_content = results.get(f"weekly_{wd}", "").strip()
        if weekly_content:
            # 提取周报的洞察和碎片连线
            insight_section = ""
            if "## 💡 本周洞察" in weekly_content:
                insight_section = weekly_content.split("## 💡 本周洞察")[1]
                end_idx = insight_section.find("\n## ")
                if end_idx >= 0:
                    insight_section = insight_section[:end_idx]
            connections_section = ""
            if "## 🔗 碎片连线" in weekly_content:
                connections_section = weekly_content.split("## 🔗 碎片连线")[1]
                end_idx = connections_section.find("\n## ")
                if end_idx >= 0:
                    connections_section = connections_section[:end_idx]
            weekly_summaries.append({
                "week_start": wd,
                "insight": insight_section.strip()[:300],
                "connections": connections_section.strip()[:500],
            })

    # 情绪评分
    mood_scores = []
    for entry in state.get("mood_scores", []):
        if entry.get("date", "")[:7] == month_str:
            mood_scores.append(entry)

    # 决策日志统计
    decision_stats = _extract_decision_stats(results["decisions"], set(dates))

    # 打卡统计
    checkin_stats = state.get("checkin_stats", {})

    return {
        "notes": notes,
        "mood_scores": mood_scores,
        "decision_stats": decision_stats,
        "weekly_summaries": weekly_summaries,
        "checkin_stats": checkin_stats,
        "record_days": record_days,
        "total_days": len(dates),
        "dates": dates,
    }


def _extract_date_entries(text, date_str):
    """从 Markdown 文件中提取指定日期的条目"""
    if not text:
        return ""
    entries = []
    sections = text.split("\n## ")
    for section in sections[1:]:
        first_line = section.split("\n")[0].strip()
        if first_line.startswith(date_str):
            entries.append("## " + section.strip())
    return "\n\n".join(entries)


def _extract_decision_stats(text, date_set):
    """从 JSONL 决策日志中统计本月 skill 使用分布"""
    if not text:
        return {}
    skill_counts = {}
    total = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "")
            if ts[:10] in date_set:
                skill = entry.get("skill", "unknown")
                skill_counts[skill] = skill_counts.get(skill, 0) + 1
                total += 1
        except Exception:
            pass
    return {"skill_counts": skill_counts, "total_decisions": total}


def _ai_analyze_month(data, period_str, month_str, dates, call_deepseek):
    """调用 AI 分析整月数据"""
    parts = [f"分析以下 {period_str} 整月的记录，生成月度成长回顾。"]

    # 情绪评分
    if data["mood_scores"]:
        parts.append("\n【情绪评分数据】")
        for s in sorted(data["mood_scores"], key=lambda x: x.get("date", "")):
            parts.append(f"- {s.get('date','?')}: {s.get('score','?')}/10 ({s.get('source','?')}) {s.get('label','')}")

    # 周报洞察
    if data["weekly_summaries"]:
        parts.append("\n【各周洞察摘要】")
        for ws in data["weekly_summaries"]:
            parts.append(f"--- 周 {ws['week_start']} ---")
            if ws["insight"]:
                parts.append(f"洞察: {ws['insight']}")
            if ws["connections"]:
                parts.append(f"连线: {ws['connections']}")

    # 决策统计
    stats = data["decision_stats"]
    if stats.get("total_decisions"):
        parts.append(f"\n【本月互动统计】共 {stats['total_decisions']} 次决策")
        for skill, count in sorted(stats["skill_counts"].items(), key=lambda x: -x[1])[:10]:
            parts.append(f"- {skill}: {count}次")

    # 打卡统计
    cs = data["checkin_stats"]
    if cs.get("total"):
        parts.append(f"\n【打卡统计】总计 {cs.get('total',0)} 次, 当前连续 {cs.get('streak',0)} 天")

    # 数据概览
    parts.append(f"\n【记录概览】记录天数 {data['record_days']}/{data['total_days']} ({round(data['record_days']/max(data['total_days'],1)*100)}%)")

    # 各天记录
    if data["notes"]:
        parts.append(f"\n【本月记录】\n{data['notes']}")

    import prompts
    parts.append(prompts.MONTHLY_JSON_FORMAT)

    prompt = "\n".join(parts)

    response = call_deepseek([
        {"role": "system", "content": prompts.MONTHLY_SYSTEM},
        {"role": "user", "content": prompt}
    ], max_tokens=1500, temperature=0.7)

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
    _log(f"[monthly.review] AI 分析 JSON 解析失败: {text[:200]}")
    return None


def _build_monthly_review(period_str, month_str, analysis, data):
    """构建月度回顾 Markdown"""
    mood_calendar = analysis.get("mood_calendar", [])
    mood_avg = analysis.get("mood_avg", "?")
    trends = analysis.get("trends", [])
    highlights = analysis.get("highlights", [])
    lowpoints = analysis.get("lowpoints", [])
    people_changes = analysis.get("people_changes", [])
    stats = analysis.get("stats", {})
    insight = analysis.get("insight", "")
    suggestions = analysis.get("next_month_suggestions", [])

    lines = [
        "---",
        "type: monthly-review",
        f"period: {month_str}",
        f"mood_avg: {mood_avg}",
        f"total_messages: {stats.get('total_messages', 0)}",
        "generated: true",
        "---",
        "",
        f"# 📊 月度回顾 · {period_str}",
        "",
    ]

    # 情绪月历
    lines.extend([
        "## 🌡️ 情绪月历",
        "",
        "| 日期 | 评分 | 来源 | 关键词 |",
        "|------|:----:|------|--------|",
    ])

    score_map = {s.get("date"): s for s in data.get("mood_scores", [])}
    for item in mood_calendar:
        d = item.get("date", "")
        score = item.get("score")
        keyword = item.get("keyword", "")
        # 匹配完整日期
        full_date = None
        for date_str in data.get("dates", []):
            if date_str.endswith(d) or d in date_str:
                full_date = date_str
                break
        source_info = score_map.get(full_date, {})
        source = source_info.get("source", "AI")
        score_str = str(score) if score is not None else "-"
        lines.append(f"| {d} | {score_str} | {source} | {keyword} |")

    # 月均 + 最高最低
    valid_scores = [item for item in mood_calendar if item.get("score") is not None]
    if valid_scores:
        highest = max(valid_scores, key=lambda x: x["score"])
        lowest = min(valid_scores, key=lambda x: x["score"])
        lines.append("")
        lines.append(f"月均：{mood_avg} · 最高：{highest['date']}({highest['score']}) · 最低：{lowest['date']}({lowest['score']})")
    lines.append("")

    # 趋势
    if trends:
        lines.extend(["## 📈 趋势", ""])
        for t in trends:
            lines.append(f"- {t}")
        lines.append("")

    # 高光时刻
    if highlights:
        lines.extend(["## 🏆 高光时刻", ""])
        for i, h in enumerate(highlights, 1):
            lines.append(f"{i}. {h.get('date', '')}: {h.get('event', '')}")
        lines.append("")

    # 低谷时刻
    if lowpoints:
        lines.extend(["## 🌧️ 低谷时刻", ""])
        for i, l in enumerate(lowpoints, 1):
            lines.append(f"{i}. {l.get('date', '')}: {l.get('event', '')}")
        lines.append("")

    # 人际关系变化
    if people_changes:
        lines.extend(["## 👥 人际关系变化", ""])
        for p in people_changes:
            lines.append(f"- **{p.get('name', '')}**：{p.get('change', '')}")
        lines.append("")

    # 数据看板
    lines.extend(["## 📊 数据看板", "", "| 指标 | 数值 |", "|------|------|"])

    total_messages = stats.get("total_messages", 0)
    record_days = data.get("record_days", 0)
    total_days = data.get("total_days", 0)
    record_pct = round(record_days / max(total_days, 1) * 100)
    categories = stats.get("categories", {})
    keywords = stats.get("keywords", [])

    lines.append(f"| 总消息数 | {total_messages} |")
    lines.append(f"| 记录天数 | {record_days}/{total_days} ({record_pct}%) |")

    checkin_stats = data.get("checkin_stats", {})
    if checkin_stats.get("total"):
        lines.append(f"| 打卡次数 | {checkin_stats['total']} |")
        lines.append(f"| 打卡连续 | {checkin_stats.get('streak', 0)} 天 |")

    decision_stats = data.get("decision_stats", {})
    if decision_stats.get("total_decisions"):
        lines.append(f"| 互动次数 | {decision_stats['total_decisions']} |")

    if categories:
        cat_parts = [f"{_cat_label(k)} {v}%" for k, v in categories.items()]
        lines.append(f"| 归档分类 | {' · '.join(cat_parts)} |")

    if keywords:
        lines.append(f"| 关键词 | {' · '.join(keywords)} |")

    lines.append("")

    # 月度洞察
    if insight:
        lines.extend([
            "## 💡 月度洞察",
            "",
            insight,
            "",
        ])

    # 下月建议
    if suggestions:
        lines.extend(["## 🎯 下月建议", ""])
        for s in suggestions:
            lines.append(f"- [ ] {s}")
        lines.append("")

    # 尾部
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    mood_count = len(data.get("mood_scores", []))
    lines.extend([
        "---",
        "",
        f"*🤖 基于 {total_messages} 条消息 + {mood_count} 天情绪数据自动生成于 {now_str}*",
    ])

    return "\n".join(lines)


def _cat_label(key):
    """归档分类 key → 中文标签"""
    labels = {"fun": "生活趣事", "emotion": "情感日记", "work": "工作笔记", "misc": "碎碎念"}
    return labels.get(key, key)


# Skill 热加载注册表
SKILL_REGISTRY = {
    "monthly.review": execute,
}
