# -*- coding: utf-8 -*-
"""
Skill: habit_coach (V3-F11)
习惯干预系统 — 基于 V2 数据模式自动设计微习惯实验，追踪执行，评估效果。

工作方式：
1. 每周一 morning_report 时，LLM 可调用 habit.propose 提议新实验
2. 日常聊天中，LLM 检测到触发条件后调用 habit.nudge 提议微行动
3. 用户可以主动调用 habit.status 查看当前实验进度
4. 每周 weekly_review 时自动汇总实验数据（由 weekly_review.py 读取 state）

state.active_experiment 结构：
{
    "name": "手机替代实验",
    "start_date": "2026-02-17",
    "end_date": "2026-02-23",
    "hypothesis": "当晚上想刷手机时，改为做15分钟创造性活动，第二天情绪评分会更高",
    "triggers": ["好无聊", "刷手机", "刷抖音"],
    "micro_action": "15分钟vibecoding或和Karvis聊天",
    "tracking": {
        "trigger_count": 0,
        "accepted_count": 0,
        "declined_count": 0,
        "entries": []
    },
    "status": "active"
}
"""
import sys
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def propose(params, state, ctx):
    """
    habit.propose — LLM 提议一个新的微习惯实验。
    由 LLM 在 morning_report(周一) 或用户主动要求时调用。
    
    params:
        name: str — 实验名称
        hypothesis: str — 假设
        triggers: list[str] — 触发关键词
        micro_action: str — 微行动描述
        duration_days: int — 持续天数（默认7）
    """
    name = params.get("name", "未命名实验")
    hypothesis = params.get("hypothesis", "")
    triggers = params.get("triggers", [])
    micro_action = params.get("micro_action", "")
    duration = params.get("duration_days", 7)

    today = datetime.now(BEIJING_TZ)
    today_str = today.strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=duration)).strftime("%Y-%m-%d")

    # 如果已有活跃实验，先归档
    existing = state.get("active_experiment")
    if existing and existing.get("status") == "active":
        _archive_experiment(state, "replaced")

    experiment = {
        "name": name,
        "start_date": today_str,
        "end_date": end_date,
        "hypothesis": hypothesis,
        "triggers": triggers,
        "micro_action": micro_action,
        "tracking": {
            "trigger_count": 0,
            "accepted_count": 0,
            "declined_count": 0,
            "entries": []
        },
        "status": "active"
    }

    state["active_experiment"] = experiment
    _log(f"[habit_coach] 新实验: {name}, 持续到 {end_date}")

    triggers_str = "、".join(triggers) if triggers else "无特定触发词"
    reply = (
        f"🧪 新实验启动！\n\n"
        f"📋 {name}\n"
        f"💡 假设：{hypothesis}\n"
        f"⚡ 触发词：{triggers_str}\n"
        f"🎯 微行动：{micro_action}\n"
        f"📅 {today_str} → {end_date}（{duration}天）\n\n"
        f"当我检测到触发条件时会提醒你~"
    )

    return {"success": True, "reply": reply}


def nudge(params, state, ctx):
    """
    habit.nudge — LLM 检测到触发条件时调用，提议微行动。
    
    params:
        trigger_text: str — 用户说了什么触发了实验
        accepted: bool|None — 如果是用户回复接受/拒绝，传入 True/False
    """
    exp = state.get("active_experiment")
    if not exp or exp.get("status") != "active":
        return {"success": True, "reply": "目前没有进行中的实验哦~"}

    trigger_text = params.get("trigger_text", "")
    accepted = params.get("accepted")

    tracking = exp.setdefault("tracking", {
        "trigger_count": 0, "accepted_count": 0,
        "declined_count": 0, "entries": []
    })
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

    if accepted is None:
        # 触发检测：记录一次触发，提议微行动
        tracking["trigger_count"] = tracking.get("trigger_count", 0) + 1
        tracking.setdefault("entries", []).append({
            "time": now_str,
            "trigger": trigger_text,
            "result": "pending"
        })

        micro_action = exp.get("micro_action", "试试做点不一样的事")
        reply = (
            f"🔔 检测到实验触发~\n\n"
            f"你的实验「{exp['name']}」建议：\n"
            f"👉 {micro_action}\n\n"
            f"要试试吗？"
        )
        return {"success": True, "reply": reply}

    else:
        # 用户回复了接受/拒绝
        entries = tracking.get("entries", [])
        if accepted:
            tracking["accepted_count"] = tracking.get("accepted_count", 0) + 1
            if entries:
                entries[-1]["result"] = "accepted"
            reply = f"💪 太棒了！去做吧~ 完成后告诉我感觉怎么样"
        else:
            tracking["declined_count"] = tracking.get("declined_count", 0) + 1
            if entries:
                entries[-1]["result"] = "declined"
            reply = f"没关系，下次再试~ 实验本来就是探索"

        return {"success": True, "reply": reply}


def status(params, state, ctx):
    """
    habit.status — 查看当前实验进度。
    """
    exp = state.get("active_experiment")
    if not exp:
        # 也展示历史实验
        history = state.get("experiment_history", [])
        if history:
            last = history[-1]
            return {
                "success": True,
                "reply": (
                    f"目前没有进行中的实验。\n\n"
                    f"上一个实验「{last.get('name', '?')}」"
                    f"已于 {last.get('end_date', '?')} 结束，"
                    f"结果：{last.get('result_summary', '未总结')}"
                )
            }
        return {"success": True, "reply": "目前没有进行中的实验，也没有历史记录。想开始一个吗？"}

    tracking = exp.get("tracking", {})
    trigger_count = tracking.get("trigger_count", 0)
    accepted = tracking.get("accepted_count", 0)
    declined = tracking.get("declined_count", 0)

    today = datetime.now(BEIJING_TZ).date()
    start = exp.get("start_date", "")
    end = exp.get("end_date", "")
    days_left = 0
    if end:
        try:
            end_dt = datetime.strptime(end, "%Y-%m-%d").date()
            days_left = max(0, (end_dt - today).days)
        except Exception:
            pass

    acceptance_rate = f"{accepted}/{trigger_count}" if trigger_count > 0 else "暂无数据"

    reply = (
        f"🧪 实验进度：{exp.get('name', '未命名')}\n\n"
        f"📅 {start} → {end}（还剩 {days_left} 天）\n"
        f"💡 {exp.get('hypothesis', '')}\n\n"
        f"📊 数据：\n"
        f"  触发次数：{trigger_count}\n"
        f"  接受微行动：{acceptance_rate}\n"
        f"  拒绝次数：{declined}\n"
    )

    return {"success": True, "reply": reply}


def complete(params, state, ctx):
    """
    habit.complete — 手动结束实验（或由定时逻辑调用）。
    
    params:
        result_summary: str — LLM 生成的实验总结
        success: bool — 实验是否成功
    """
    exp = state.get("active_experiment")
    if not exp:
        return {"success": True, "reply": "没有进行中的实验~"}

    result_summary = params.get("result_summary", "")
    is_success = params.get("success", None)

    _archive_experiment(state, "completed", result_summary, is_success)

    status_emoji = "✅" if is_success else ("❌" if is_success is False else "📋")
    reply = (
        f"{status_emoji} 实验「{exp.get('name', '')}」已结束！\n\n"
        f"{result_summary}" if result_summary else
        f"{status_emoji} 实验「{exp.get('name', '')}」已结束！"
    )

    return {"success": True, "reply": reply}


def _archive_experiment(state, reason, result_summary="", is_success=None):
    """将当前实验归档到 experiment_history"""
    exp = state.get("active_experiment")
    if not exp:
        return

    exp["status"] = reason  # completed / replaced / expired
    exp["result_summary"] = result_summary
    exp["is_success"] = is_success

    history = state.setdefault("experiment_history", [])
    # 只保留最近 10 个实验
    history.append(exp)
    if len(history) > 10:
        state["experiment_history"] = history[-10:]

    state["active_experiment"] = None
    _log(f"[habit_coach] 实验归档: {exp.get('name', '?')}, reason={reason}")


def get_experiment_summary_for_review(state):
    """
    供 weekly_review.py 调用：获取本周实验的汇总数据。
    返回 dict 或 None。
    """
    exp = state.get("active_experiment")
    if not exp or exp.get("status") != "active":
        return None

    tracking = exp.get("tracking", {})
    return {
        "name": exp.get("name", ""),
        "hypothesis": exp.get("hypothesis", ""),
        "micro_action": exp.get("micro_action", ""),
        "trigger_count": tracking.get("trigger_count", 0),
        "accepted_count": tracking.get("accepted_count", 0),
        "declined_count": tracking.get("declined_count", 0),
        "days_active": _days_since(exp.get("start_date", "")),
        "days_remaining": _days_until(exp.get("end_date", "")),
    }


def check_experiment_expiry(state):
    """
    供 app.py 定时调用：检查实验是否已过期。
    如果过期，归档并返回提示消息；否则返回 None。
    """
    exp = state.get("active_experiment")
    if not exp or exp.get("status") != "active":
        return None

    end_date = exp.get("end_date", "")
    if not end_date:
        return None

    today = datetime.now(BEIJING_TZ).date()
    try:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        if today > end_dt:
            name = exp.get("name", "未命名")
            tracking = exp.get("tracking", {})
            _archive_experiment(state, "expired")
            return (
                f"🧪 实验「{name}」已到期！\n"
                f"📊 触发 {tracking.get('trigger_count', 0)} 次，"
                f"接受 {tracking.get('accepted_count', 0)} 次\n"
                f"想总结一下这个实验的效果吗？"
            )
    except Exception:
        pass
    return None


def _days_since(date_str):
    if not date_str:
        return 0
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(BEIJING_TZ).date()
        return max(0, (today - dt).days)
    except Exception:
        return 0


def _days_until(date_str):
    if not date_str:
        return 0
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(BEIJING_TZ).date()
        return max(0, (dt - today).days)
    except Exception:
        return 0


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "habit.propose": propose,
    "habit.nudge": nudge,
    "habit.status": status,
    "habit.complete": complete,
}
