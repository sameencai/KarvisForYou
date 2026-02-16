# -*- coding: utf-8 -*-
"""
Skill: checkin.*
每日打卡流程：4 个问题，顺序推进，写入 Daily Note。

打卡状态字段（存在 .ai-life-state.json 中，由 brain.py 管理读写）：
    checkin_pending: bool
    checkin_step: int (1-4)
    checkin_answers: list[{"q": str, "a": str, "score": int?}]
    checkin_sent_at: str (YYYY-MM-DD HH:MM)
    checkin_date: str (YYYY-MM-DD)
"""
import re
import sys
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


BEIJING_TZ = timezone(timedelta(hours=8))

CHECKIN_QUESTIONS = [
    {"id": "q1", "question": "今天做了什么？", "type": "text"},
    {"id": "q2", "question": "今天状态打几分？(1-10)", "type": "score"},
    {"id": "q3", "question": "什么事让你纠结？", "type": "text"},
    {"id": "q4", "question": "脑子里最常冒出的念头是什么？", "type": "text"},
]


# ============ Skill 入口函数 ============

def start(params, state, ctx):
    """
    启动打卡流程。
    由 LLM 触发（system 消息 evening_checkin 或用户主动说"打卡"）。

    returns:
        {"success": bool, "reply": str, "state_updates": dict}
    """
    if state.get("checkin_pending"):
        step = state.get("checkin_step", 1)
        q = CHECKIN_QUESTIONS[step - 1]["question"]
        return {
            "success": True,
            "reply": f"打卡已在进行中~ ({step}/4)\n\n{q}"
        }

    now = datetime.now(BEIJING_TZ)
    return {
        "success": True,
        "reply": f"🌙 开始今日复盘 (1/4)\n\n{CHECKIN_QUESTIONS[0]['question']}",
        "state_updates": {
            "checkin_pending": True,
            "checkin_step": 1,
            "checkin_answers": [],
            "checkin_sent_at": now.strftime("%Y-%m-%d %H:%M"),
            "checkin_date": now.strftime("%Y-%m-%d")
        }
    }


def answer(params, state, ctx):
    """
    处理打卡回答。

    params:
        answer: str — 用户的回答内容
        step: int — 当前题号（LLM 传入，用于校验）

    returns:
        {"success": bool, "reply": str, "state_updates": dict}
    """
    if not state.get("checkin_pending"):
        return {"success": False, "reply": "当前没有进行中的打卡"}

    step = state.get("checkin_step", 0)
    if step < 1 or step > 4:
        return {"success": False, "reply": "打卡状态异常，请重新开始"}

    answer_text = params.get("answer", "").strip()
    if not answer_text:
        return {"success": True, "reply": "回答不能为空哦~"}

    current_q = CHECKIN_QUESTIONS[step - 1]

    # Q2 特殊处理：评分题（用宽松正则，兼容中文环境如 "8分"、"8 分"）
    score = None
    if current_q["type"] == "score":
        match = re.search(r'(?<!\d)(10|[1-9])(?!\d)', answer_text)
        if match:
            score = int(match.group())
        else:
            return {"success": True, "reply": "请回复 1-10 的数字评分~"}

    # 记录回答
    record = {"q": current_q["question"], "a": answer_text}
    if score is not None:
        record["score"] = score

    answers = state.get("checkin_answers", [])[:]
    answers.append(record)

    # 推进到下一题 or 完成
    if step < 4:
        next_q = CHECKIN_QUESTIONS[step]
        return {
            "success": True,
            "reply": f"✓ 已记录 ({step + 1}/4)\n\n{next_q['question']}",
            "state_updates": {
                "checkin_step": step + 1,
                "checkin_answers": answers
            }
        }
    else:
        # 全部回答完毕，写入 Daily Note
        state["checkin_answers"] = answers  # 临时更新用于 finish
        reply = finish(state, ctx, timeout=False)
        return {
            "success": True,
            "reply": reply,
            "state_updates": {
                "checkin_pending": False,
                "checkin_step": 0,
                "checkin_answers": []
            }
        }


def skip(params, state, ctx):
    """
    跳过当前打卡问题。

    returns:
        {"success": bool, "reply": str, "state_updates": dict}
    """
    if not state.get("checkin_pending"):
        return {"success": False, "reply": "当前没有进行中的打卡"}

    step = state.get("checkin_step", 0)
    if step < 1 or step > 4:
        return {"success": False, "reply": "打卡状态异常"}

    current_q = CHECKIN_QUESTIONS[step - 1]

    # 记录为跳过
    record = {"q": current_q["question"], "a": "（跳过）"}
    answers = state.get("checkin_answers", [])[:]
    answers.append(record)

    if step < 4:
        next_q = CHECKIN_QUESTIONS[step]
        return {
            "success": True,
            "reply": f"⏭️ 已跳过 ({step + 1}/4)\n\n{next_q['question']}",
            "state_updates": {
                "checkin_step": step + 1,
                "checkin_answers": answers
            }
        }
    else:
        state["checkin_answers"] = answers
        reply = finish(state, ctx, timeout=False)
        return {
            "success": True,
            "reply": reply,
            "state_updates": {
                "checkin_pending": False,
                "checkin_step": 0,
                "checkin_answers": []
            }
        }


def cancel(params, state, ctx):
    """
    取消当前打卡。

    returns:
        {"success": bool, "reply": str, "state_updates": dict}
    """
    if not state.get("checkin_pending"):
        return {"success": True, "reply": "当前没有打卡在进行哦"}

    answered = len(state.get("checkin_answers", []))
    _log(f"[checkin] 取消打卡，已回答 {answered} 题")

    return {
        "success": True,
        "reply": "❌ 已取消今日打卡",
        "state_updates": {
            "checkin_pending": False,
            "checkin_step": 0,
            "checkin_answers": []
        }
    }


# ============ 打卡完成：写入 Daily Note ============

def finish(state, ctx, timeout=False):
    """
    完成打卡，将回答写入 Daily Note，返回通知文本。
    这不是 LLM 直接触发的 Skill，而是由 answer/skip 内部调用或 brain.py 超时调用。
    """
    answers = state.get("checkin_answers", [])
    checkin_date = state.get("checkin_date",
                             datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"))

    if not answers:
        # 清除状态
        state["checkin_pending"] = False
        return "打卡无回答，已取消"

    # 提取情绪分
    score = None
    for ans in answers:
        if "score" in ans:
            score = ans["score"]
            break

    # 构建 Markdown
    lines = ["## 每日复盘\n"]
    for i, ans in enumerate(answers):
        q_text = ans.get("q", f"Q{i+1}")
        a_text = ans.get("a", "")
        if "score" in ans:
            a_text = f"{ans['score']}/10"
            if ans.get("a") and ans["a"] != str(ans["score"]):
                a_text += f" ({ans['a']})"
        lines.append(f"### Q{i+1}. {q_text}")
        lines.append(a_text)
        lines.append("")

    checkin_content = "\n".join(lines)

    # 写入 Daily Note
    daily_note_path = f"{ctx.daily_notes_dir}/{checkin_date}.md"
    _write_to_daily_note(daily_note_path, checkin_date, checkin_content)

    # 记录 mood_scores
    if score is not None:
        scores = state.setdefault("mood_scores", [])
        # 去重：同一天只保留最新（打卡 > 自动）
        scores = [s for s in scores if s.get("date") != checkin_date]
        scores.append({
            "date": checkin_date,
            "score": score,
            "source": "checkin"
        })
        state["mood_scores"] = scores

    # 更新打卡统计（F8：打卡数据深度利用）
    stats = state.setdefault("checkin_stats", {"total": 0, "streak": 0, "last_checkin_date": ""})
    stats["total"] = stats.get("total", 0) + 1
    last_date = stats.get("last_checkin_date", "")
    if last_date:
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d").date()
            today_dt = datetime.strptime(checkin_date, "%Y-%m-%d").date()
            if (today_dt - last_dt).days == 1:
                stats["streak"] = stats.get("streak", 0) + 1
            elif (today_dt - last_dt).days > 1:
                stats["streak"] = 1
            # 同一天重复打卡不改 streak
        except Exception:
            stats["streak"] = 1
    else:
        stats["streak"] = 1
    stats["last_checkin_date"] = checkin_date

    # 清除打卡状态
    state["checkin_pending"] = False
    state["checkin_step"] = 0
    state["checkin_answers"] = []

    # 返回完成通知
    if timeout:
        return f"⏰ 打卡超时，已保存 {len(answers)} 条回答到 {checkin_date}.md"
    else:
        score_text = f"状态: {score}/10 " if score else ""
        return f"✅ 今日复盘完成！{score_text}\n已保存到 {checkin_date}.md\n晚安~"


def _write_to_daily_note(file_path, date_str, checkin_content):
    """将打卡内容写入 Daily Note（替换或追加 ## 每日复盘 section）"""
    existing = OneDriveIO.read_text(file_path)

    if existing is None:
        _log(f"[checkin] 无法读取 Daily Note，尝试创建: {file_path}")
        existing = ""

    if existing:
        if "## 每日复盘" in existing:
            # 替换已有的 section
            parts = existing.split("## 每日复盘")
            before = parts[0]
            after_parts = parts[1].split("\n## ", 1)
            after = "\n## " + after_parts[1] if len(after_parts) > 1 else ""
            new_content = before + checkin_content + after
        else:
            # 追加到末尾
            new_content = existing.rstrip() + "\n\n" + checkin_content
    else:
        # 全新的 Daily Note
        new_content = f"# {date_str}\n\n{checkin_content}"

    ok = OneDriveIO.write_text(file_path, new_content)
    if ok:
        _log(f"[checkin] 已写入 Daily Note: {file_path}")
    else:
        _log(f"[checkin] 写入 Daily Note 失败: {file_path}")
    return ok


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "checkin.start": start,
    "checkin.answer": answer,
    "checkin.skip": skip,
    "checkin.cancel": cancel,
}
