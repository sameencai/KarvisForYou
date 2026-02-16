# -*- coding: utf-8 -*-
"""
Skill: decision_track (V3-F15)
决策复盘系统 — 记录重要决策 → 设定复盘时间 → 到期自动询问结果。

工作方式：
1. LLM 检测到"决策时刻"（纠结/犹豫/要不要）→ 调用 decision.record
2. 到期时 morning_report 注入到期决策 → LLM 在早报中自然询问
3. 用户可以主动查看 decision.list
4. 用户回复决策结果后 LLM 调用 decision.review 写入结果

state.pending_decisions 结构：
[
    {
        "id": "d001",
        "date": "2026-02-11",
        "topic": "要不要换工作",
        "decision": "先观望",
        "emotion": "纠结",
        "review_date": "2026-02-14",
        "result": null,
        "result_date": null,
        "result_feeling": null
    }
]
"""
import sys
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _gen_id(state):
    """生成简单递增 ID"""
    decisions = state.get("pending_decisions", [])
    history = state.get("decision_history", [])
    max_id = 0
    for d in decisions + history:
        did = d.get("id", "d000")
        try:
            num = int(did.replace("d", ""))
            if num > max_id:
                max_id = num
        except Exception:
            pass
    return f"d{max_id + 1:03d}"


def record(params, state, ctx):
    """
    decision.record — 记录一个决策。
    
    params:
        topic: str — 决策主题（如"要不要换工作"）
        decision: str — 最终决定（如"先观望"）
        emotion: str — 当时的情绪（如"纠结"）
        review_days: int — 几天后复盘（默认3）
    """
    topic = params.get("topic", "")
    decision_text = params.get("decision", "")
    emotion = params.get("emotion", "")
    review_days = params.get("review_days", 3)

    if not topic:
        return {"success": False, "reply": "决策主题不能为空~"}

    today = datetime.now(BEIJING_TZ)
    today_str = today.strftime("%Y-%m-%d")
    review_date = (today + timedelta(days=review_days)).strftime("%Y-%m-%d")

    decisions = state.setdefault("pending_decisions", [])
    did = _gen_id(state)

    entry = {
        "id": did,
        "date": today_str,
        "topic": topic,
        "decision": decision_text,
        "emotion": emotion,
        "review_date": review_date,
        "result": None,
        "result_date": None,
        "result_feeling": None,
    }
    decisions.append(entry)

    _log(f"[decision_track] 记录决策: {did} — {topic}, 复盘日 {review_date}")

    emotion_str = f"（{emotion}）" if emotion else ""
    reply = (
        f"📝 记住了这个决策~\n\n"
        f"「{topic}」→ {decision_text}{emotion_str}\n"
        f"📅 {review_days} 天后（{review_date}）我会问你后来怎么样了"
    )

    return {"success": True, "reply": reply}


def review(params, state, ctx):
    """
    decision.review — 用户回复决策结果。
    
    params:
        decision_id: str — 决策 ID（如 "d001"，可选，默认取最近到期的）
        result: str — 结果描述（如"做对了" / "后悔了" / "还在进行中"）
        feeling: str — 现在的感受
    """
    decision_id = params.get("decision_id", "")
    result = params.get("result", "")
    feeling = params.get("feeling", "")

    decisions = state.get("pending_decisions", [])

    # 找到目标决策
    target = None
    if decision_id:
        for d in decisions:
            if d.get("id") == decision_id:
                target = d
                break
    else:
        # 默认取最近到期的（review_date 最早且未完成的）
        today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        candidates = [d for d in decisions if not d.get("result") and d.get("review_date", "9999") <= today_str]
        if candidates:
            target = min(candidates, key=lambda x: x.get("review_date", "9999"))
        elif decisions:
            # 没有到期的，取最近一个未完成的
            pending = [d for d in decisions if not d.get("result")]
            if pending:
                target = pending[-1]

    if not target:
        return {"success": True, "reply": "没有找到待复盘的决策~"}

    # 更新结果
    today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    target["result"] = result
    target["result_date"] = today_str
    target["result_feeling"] = feeling

    # 移到历史
    _archive_decision(state, target)

    _log(f"[decision_track] 复盘完成: {target['id']} — {result}")

    reply = (
        f"📋 决策复盘完成~\n\n"
        f"「{target['topic']}」\n"
        f"当时决定：{target.get('decision', '')}\n"
        f"结果：{result}\n"
    )
    if feeling:
        reply += f"现在的感受：{feeling}\n"

    return {"success": True, "reply": reply}


def list_decisions(params, state, ctx):
    """
    decision.list — 查看待复盘的决策。
    """
    decisions = state.get("pending_decisions", [])
    pending = [d for d in decisions if not d.get("result")]

    if not pending:
        history = state.get("decision_history", [])
        if history:
            last = history[-1]
            return {
                "success": True,
                "reply": (
                    f"没有待复盘的决策。\n\n"
                    f"最近一次复盘：「{last.get('topic', '?')}」"
                    f"→ {last.get('result', '?')}（{last.get('result_date', '?')}）"
                )
            }
        return {"success": True, "reply": "还没有记录过决策~ 有纠结的事可以告诉我"}

    lines = ["📋 待复盘的决策：\n"]
    today = datetime.now(BEIJING_TZ).date()
    for d in pending:
        review_date = d.get("review_date", "")
        topic = d.get("topic", "")
        decision_text = d.get("decision", "")
        emotion = d.get("emotion", "")

        # 计算是否到期
        overdue = ""
        if review_date:
            try:
                rd = datetime.strptime(review_date, "%Y-%m-%d").date()
                if today >= rd:
                    overdue = " ⏰ 到期"
                else:
                    days_left = (rd - today).days
                    overdue = f" ({days_left}天后复盘)"
            except Exception:
                pass

        emotion_str = f"（{emotion}）" if emotion else ""
        lines.append(f"• {d.get('date', '')} 「{topic}」→ {decision_text}{emotion_str}{overdue}")

    return {"success": True, "reply": "\n".join(lines)}


def _archive_decision(state, decision):
    """将已复盘的决策从 pending 移到 history"""
    decisions = state.get("pending_decisions", [])
    state["pending_decisions"] = [d for d in decisions if d.get("id") != decision.get("id")]

    history = state.setdefault("decision_history", [])
    history.append(decision)
    # 只保留最近 20 条历史
    if len(history) > 20:
        state["decision_history"] = history[-20:]


def get_due_decisions(state):
    """
    供 app.py morning_report 调用：获取今天到期的待复盘决策。
    返回 list[dict] 或空 list。
    """
    decisions = state.get("pending_decisions", [])
    today_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    due = []
    for d in decisions:
        if not d.get("result") and d.get("review_date", "9999") <= today_str:
            due.append({
                "id": d["id"],
                "date": d.get("date", ""),
                "topic": d.get("topic", ""),
                "decision": d.get("decision", ""),
                "emotion": d.get("emotion", ""),
                "review_date": d.get("review_date", ""),
            })
    return due


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "decision.record": record,
    "decision.review": review,
    "decision.list": list_decisions,
}
