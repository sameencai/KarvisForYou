# -*- coding: utf-8 -*-
"""
Skill: reflect.*
每日深度自问 — 每天推送一个深度问题，引导自我探索。

状态字段（存在 .ai-life-state.json 中）：
    reflect_pending: bool           — 是否有待回答的问题
    reflect_question_id: str        — 当前问题 ID（如 "fear_001"）
    reflect_question: str           — 当前问题文本
    reflect_category: str           — 当前问题维度
    reflect_sent_at: str            — 推送时间
    reflect_stats: dict             — 统计信息
"""
import os
import sys
import json
import random
from datetime import datetime, timezone, timedelta
from config import REFLECT_COOLDOWN_DAYS
from storage import IO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _reflect_dir(ctx):
    """获取用户的 reflect 数据目录"""
    d = os.path.join(ctx.base_dir, "_Karvis", "reflect")
    os.makedirs(d, exist_ok=True)
    return d


def _reflect_log_file(ctx):
    return os.path.join(_reflect_dir(ctx), "reflect_log.jsonl")


def _question_history_file(ctx):
    return os.path.join(_reflect_dir(ctx), "question_history.json")


# ============ 题库 ============

CATEGORIES = [
    "自我认知", "恐惧与安全感", "内在对话", "人际关系", "时间与优先级",
    "欲望与动力", "情绪与疗愈", "价值观", "成长与变化", "梦想与想象",
]

CATEGORY_EMOJI = {
    "自我认知": "🪞", "恐惧与安全感": "😰", "内在对话": "💭",
    "人际关系": "🫂", "时间与优先级": "⏳", "欲望与动力": "🔥",
    "情绪与疗愈": "🩹", "价值观": "🧭", "成长与变化": "🌱",
    "梦想与想象": "🌙",
}

QUESTION_BANK = {
    "自我认知": [
        "用三个词形容自己，你会选什么？",
        "别人眼中的你和你自己认为的你，最大的差别是什么？",
        "你最不愿意承认的一个缺点是什么？",
        "你觉得自己最被低估的优点是什么？",
        "如果要给自己写一句墓志铭，你会写什么？",
        "你最像哪个虚构角色？为什么？",
        "你觉得自己"活成了想要的样子"吗？差距在哪？",
        "你习惯用什么方式保护自己？",
        "你最近一次对自己感到骄傲是什么时候？",
        "你觉得自己最矛盾的一面是什么？",
        "如果有一个完全了解你的人，你觉得 TA 会怎样评价你？",
        "你最害怕别人发现你的哪一面？",
        "你对自己最常说的一句批评是什么？",
        "你身上哪个特质是你最想传给孩子的？",
        "你最近一次违背自己原则的时刻是什么？",
        "你觉得自己是一个"真实"的人吗？",
        "如果可以重新设计自己的性格，你会改什么？",
        "你在什么时候最像"真正的自己"？",
        "你最常伪装的情绪是什么？",
        "你觉得"了解自己"这件事，你做到了百分之几？",
    ],
    "恐惧与安全感": [
        "你内心最恐惧的三种事物是什么？",
        "你最怕失去什么？",
        "你觉得自己"还不够好"的想法来自哪里？",
        "什么时候你会感到最不安全？",
        "你有没有一个反复出现的噩梦或担忧？",
        "你最害怕的一种人际关系模式是什么？",
        "如果明天就是世界末日，你最遗憾什么？",
        "你觉得自己配得上现在拥有的一切吗？",
        "你害怕被遗忘还是害怕被记住？",
        "你最常回避的话题是什么？",
        "什么事情会让你觉得"一切都完了"？",
        "你害怕变老吗？最怕老去后的什么？",
        "你最近一次感到"失控"是什么时候？",
        "你觉得自己最脆弱的时刻是什么样的？",
        "有没有一个你一直不敢做的决定？",
        "你害怕孤独还是害怕不被理解？",
        "如果可以消除一种恐惧，你选哪个？",
        "你觉得安全感来自外在（钱/关系）还是内在（自信/信念）？",
        "你小时候害怕的事情，现在还怕吗？",
        "你有没有因为害怕而放弃过什么重要的事？",
    ],
    "内在对话": [
        "你脑子里最常冒出的一句话是什么？",
        "你对自己说话的语气，通常是鼓励的还是批评的？",
        "当你犯错时，内心第一个念头是什么？",
        "你有没有一句话会反复对自己说来安慰自己？",
        "深夜一个人的时候，你的脑海里通常在想什么？",
        "你的内心独白里，最常出现哪个人？",
        "你觉得自己内心有几个"声音"？它们分别在说什么？",
        "当你需要做重大决定时，内心的对话是怎样的？",
        "你有没有一个想法，是你从来没告诉过任何人的？",
        "你最常对自己撒的谎是什么？",
        "你的内心深处，觉得自己值得被爱吗？",
        "如果内心的声音是一个人，TA 长什么样？",
        "你有没有一句话，是你一直想说但说不出口的？",
        "你和自己的关系好吗？满分10分你打几分？",
        "你会因为别人的评价而改变对自己的看法吗？",
        "你最常在什么时候进行"内心审判"？",
        "你内心最渴望被人说的一句话是什么？",
        "你有没有给自己设过一个"禁区"——不允许自己想的事？",
        "你觉得自己是倾向于内疚还是愤怒？",
        "当你说"我没事"的时候，内心真实的感受是什么？",
    ],
    "人际关系": [
        "如果有人完全懂你，你想对 TA 说什么？",
        "你觉得谁最了解你？TA 了解你到什么程度？",
        "你最近一次感到被深深理解是什么时候？",
        "你最害怕在关系中发生什么？",
        "你通常是先付出的那一方，还是等待别人先来的？",
        "有没有一段关系，你知道该放下但一直没有？",
        "你觉得自己在关系中最大的模式是什么？",
        "你最想修复的一段关系是哪段？",
        "你会用什么方式表达爱？你希望别人用什么方式爱你？",
        "你有没有因为害怕受伤而主动推开谁？",
        "你觉得自己是一个容易信任别人的人吗？",
        "你最感激的一个人是谁？你告诉过 TA 吗？",
        "你和父母的关系，用一个词形容是什么？",
        "你觉得友情中最重要的品质是什么？",
        "你有没有一个人，是你想联系但一直没联系的？",
        "你在群体中通常扮演什么角色？",
        "你最受不了别人的什么行为？这和你自己有关系吗？",
        "你觉得"边界感"对你来说难不难？",
        "你最近一次心甘情愿为别人做了什么？",
        "你觉得自己值得拥有好的关系吗？",
    ],
    "时间与优先级": [
        "哪些事会让你产生"没有时间"的焦虑感？",
        "你最近浪费了多少时间在不重要的事上？",
        "如果今天是你的最后一天，你想怎么过？",
        "你花最多时间的三件事是什么？它们是你真正重要的事吗？",
        "你有没有一直说"以后再做"的事？",
        "你觉得自己每天最有价值的 1 小时花在了哪里？",
        "如果每天多出 2 小时，你会用来做什么？",
        "你最后悔在什么事上花了太多时间？",
        "你觉得自己的时间是"被安排的"还是"自己选择的"？",
        "你上次完全不看手机、沉浸在一件事里是什么时候？",
        "你有没有因为拖延而错过什么？",
        "你觉得"忙"和"充实"的区别是什么？",
        "你每周有多少时间是留给自己的？",
        "你觉得什么事情是"紧急但不重要"的？你能不做吗？",
        "你希望退休后的生活是什么样的？现在能做什么准备？",
        "你花在社交媒体上的时间值得吗？",
        "你有没有一项被你搁置很久的爱好？",
        "你觉得"效率"是不是被过度追求了？",
        "你理想中的一天是什么样的？",
        "你现在的生活节奏，是你想要的吗？",
    ],
    "欲望与动力": [
        "如果不考虑钱和别人的看法，你最想做什么？",
        "你最近一次感到"热血沸腾"是什么时候？",
        "你小时候的梦想是什么？现在还想实现吗？",
        "你觉得什么事情值得你全力以赴？",
        "你最羡慕别人拥有而你没有的是什么？",
        "你的欲望清单里，排第一的是什么？",
        "你做什么事的时候会忘记时间？",
        "你觉得金钱对你的真正意义是什么？",
        "如果成功率是 100%，你会去做什么？",
        "你最近放弃了什么？为什么？",
        "你觉得自己缺乏动力的时候，通常是因为什么？",
        "你有没有一个秘密的野心？",
        "你做事的动力更多来自"追求快乐"还是"逃避痛苦"？",
        "你觉得自己的"舒适区"在哪里？你想走出去吗？",
        "什么事情能让你即使很累也愿意去做？",
        "你最近一次说"算了"是在什么情境下？",
        "你觉得"欲望"是好事还是坏事？",
        "你心中有没有一件事，是"如果不做会后悔一辈子"的？",
        "你会为了什么而改变自己的生活方式？",
        "你觉得自己更缺"想清楚"还是"去行动"？",
    ],
    "情绪与疗愈": [
        "最近什么事让你感到委屈但没说出口？",
        "你上一次大哭是什么时候？因为什么？",
        "你通常怎么处理愤怒？压下去还是表达出来？",
        "你觉得什么时候的自己最脆弱？",
        "有没有一件事，你以为自己释怀了，但其实没有？",
        "你最需要被安慰的时刻是什么样的？",
        "你习惯用什么方式让自己好起来？",
        "你有没有一种情绪，是你觉得"不应该有"的？",
        "你最近一次压抑自己的情绪是什么时候？",
        "你觉得自己是容易原谅别人的人吗？",
        "你有没有对谁心存愧疚？",
        "你最需要疗愈的一段经历是什么？",
        "如果眼泪会说话，你的眼泪会说什么？",
        "你觉得"坚强"和"压抑"的界限在哪里？",
        "你有没有把快乐当成义务——觉得自己"应该"开心？",
        "你最后一次允许自己"不好"是什么时候？",
        "你觉得悲伤有价值吗？",
        "你有没有一个安全的地方，可以完全放下伪装？",
        "你最常用什么方式逃避不想面对的情绪？",
        "当你说"我很好"的时候，你的身体有什么感觉？",
    ],
    "价值观": [
        "你觉得"成功"对你来说意味着什么？",
        "你最看重的三个人生价值是什么？",
        "你愿意为什么牺牲金钱？为什么牺牲时间？",
        "你觉得什么是"好的生活"？",
        "你人生中最正确的一个决定是什么？",
        "你觉得"自由"和"安全"哪个更重要？",
        "你会因为什么而尊重一个人？",
        "你觉得"公平"真的存在吗？",
        "你人生中有没有一个不可触碰的底线？",
        "你觉得努力和运气哪个更重要？",
        "你会为了什么事情和好朋友翻脸？",
        "你觉得"善良"会不会让人吃亏？",
        "你最不能接受的社会现象是什么？",
        "你觉得人活着的意义是什么？",
        "你愿意用 10 年寿命换取什么？",
        "你觉得"正义"和"规则"冲突的时候应该怎么选？",
        "你人生中最后悔的一次妥协是什么？",
        "你觉得"真实"重要还是"得体"重要？",
        "你对"死亡"的看法是什么？",
        "你觉得什么东西是金钱绝对买不到的？",
    ],
    "成长与变化": [
        "和一年前的自己相比，你最大的变化是什么？",
        "你最近学到的一个教训是什么？",
        "你觉得自己正在变好还是变糟？哪方面？",
        "有什么事情是你以前很在意、现在不在意了的？",
        "你最近一次走出舒适区是什么时候？",
        "你觉得自己成长最快的时期是什么时候？为什么？",
        "你有没有一个曾经的"执念"，现在放下了？",
        "你觉得什么样的痛苦最能让人成长？",
        "你最想改掉的一个习惯是什么？",
        "你最近一次改变想法是因为什么？",
        "你觉得自己还有什么潜力没被开发？",
        "你接受自己的不完美吗？哪方面最难接受？",
        "回看过去的自己，你想对 TA 说什么？",
        "你觉得"成熟"意味着什么？",
        "你有没有因为某个人而改变了自己？",
        "你最近克服了什么困难？感受是什么？",
        "你觉得自己还需要学习什么？",
        "你会用什么方式衡量自己的成长？",
        "你觉得"变化"是让你兴奋还是焦虑？",
        "五年后你希望自己是什么样的？",
    ],
    "梦想与想象": [
        "如果能给 10 年后的自己写一封信，你会写什么？",
        "如果可以在任何时代生活，你选哪个？",
        "你最想拥有一种什么超能力？",
        "如果人生可以重来，你想改变什么？",
        "你的人生如果拍成电影，片名叫什么？",
        "如果可以和任何人（古今中外）吃一顿饭，你选谁？",
        "你理想中的退休生活是什么样的？",
        "如果明天醒来发现一切归零，你第一件事做什么？",
        "你希望别人在你的葬礼上说什么？",
        "如果可以许一个必然实现的愿望，你许什么？",
        "你有没有一个反复出现的白日梦？",
        "如果可以活到 200 岁，你会怎么安排人生？",
        "你最想去但还没去过的地方是哪里？为什么？",
        "如果有一天 AI 可以完全替代你的工作，你会做什么？",
        "你小时候想象的"未来的自己"和现在一样吗？",
        "如果可以向全世界广播一句话，你会说什么？",
        "你觉得"完美的一天"是什么样的？",
        "如果可以拥有另一个人的人生体验一周，你选谁？",
        "你做过最疯狂的梦是什么？",
        "如果生命只剩下一年，你的 bucket list 是什么？",
    ],
}

# 构建 question_id → (category, index, text) 映射
_QUESTION_INDEX = {}
for cat, questions in QUESTION_BANK.items():
    cat_short = cat.split("（")[0] if "（" in cat else cat
    for i, q in enumerate(questions):
        qid = f"{cat_short}_{i+1:03d}"
        _QUESTION_INDEX[qid] = (cat, i, q)


# ============ 题目选择算法 ============

def _load_question_history(ctx):
    """加载已推送问题历史"""
    path = _question_history_file(ctx)
    text = IO.read_text(path)
    if text:
        try:
            return json.loads(text)
        except Exception:
            pass
    return {"pushed": []}


def _save_question_history(history, ctx):
    IO.write_text(_question_history_file(ctx), json.dumps(history, ensure_ascii=False, indent=2))


def _select_question(state, ctx):
    """
    选择今日问题。
    策略：维度轮转 → 维度内随机 → 去重（90 天内不重复）→ 心情适配
    """
    history = _load_question_history(ctx)
    pushed = history.get("pushed", [])
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    # 构建冷却集合：90 天内已推送的 question_id
    cooldown_ids = set()
    for entry in pushed:
        entry_date = entry.get("date", "")
        try:
            d = datetime.strptime(entry_date, "%Y-%m-%d")
            t = datetime.strptime(today, "%Y-%m-%d")
            if (t - d).days < REFLECT_COOLDOWN_DAYS:
                cooldown_ids.add(entry.get("qid", ""))
        except Exception:
            pass

    # 统计各维度已回答次数，选择最少的维度优先
    stats = state.get("reflect_stats", {})
    cat_counts = stats.get("category_counts", {})

    # 心情适配：近 3 天 mood_score ≤ 4 → 优先推送疗愈类
    mood_scores = state.get("mood_scores", [])
    recent_low = False
    if mood_scores:
        recent = sorted(mood_scores, key=lambda x: x.get("date", ""), reverse=True)[:3]
        avg = sum(s.get("score", 5) for s in recent) / len(recent)
        if avg <= 4:
            recent_low = True

    # 维度轮转：按已回答次数排序（少的优先）
    if recent_low:
        priority_cats = ["情绪与疗愈", "内在对话"]
        other_cats = [c for c in CATEGORIES if c not in priority_cats]
        sorted_cats = priority_cats + sorted(other_cats, key=lambda c: cat_counts.get(c, 0))
    else:
        sorted_cats = sorted(CATEGORIES, key=lambda c: cat_counts.get(c, 0))

    # 从每个维度中尝试选择一个未冷却的问题
    for cat in sorted_cats:
        questions = QUESTION_BANK.get(cat, [])
        cat_short = cat.split("（")[0] if "（" in cat else cat
        available = []
        for i, q in enumerate(questions):
            qid = f"{cat_short}_{i+1:03d}"
            if qid not in cooldown_ids:
                available.append((qid, q))
        if available:
            qid, question = random.choice(available)
            return {
                "question_id": qid,
                "category": cat,
                "question": question,
            }

    # 所有题目都在冷却期，随机选一个
    cat = random.choice(CATEGORIES)
    questions = QUESTION_BANK[cat]
    i = random.randint(0, len(questions) - 1)
    cat_short = cat.split("（")[0] if "（" in cat else cat
    return {
        "question_id": f"{cat_short}_{i+1:03d}",
        "category": cat,
        "question": questions[i],
    }


# ============ Skill 入口函数 ============

def push(params, state, ctx):
    """
    推送今日深度自问。
    由 V8 调度触发，或用户手动触发。
    """
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    # 防重复：今天已推送过
    if state.get("reflect_pending"):
        q = state.get("reflect_question", "")
        cat = state.get("reflect_category", "")
        emoji = CATEGORY_EMOJI.get(cat, "💭")
        return {
            "success": True,
            "reply": f"{emoji} 今天的问题还没回答呢~\n\n{q}"
        }

    last_date = state.get("reflect_stats", {}).get("last_reflect_date", "")
    if last_date == today:
        return {"success": True, "reply": "今天的深度自问已经完成啦，明天见~"}

    # 打卡冲突检查
    if state.get("checkin_pending"):
        _log("[reflect.push] checkin_pending=true，跳过")
        return {"success": True, "reply": None}

    # 选题
    selected = _select_question(state, ctx)
    qid = selected["question_id"]
    cat = selected["category"]
    question = selected["question"]
    emoji = CATEGORY_EMOJI.get(cat, "💭")

    now_str = datetime.now(BEIJING_TZ).isoformat()

    # 记录到历史
    history = _load_question_history(ctx)
    history["pushed"].append({"qid": qid, "date": today})
    # 只保留最近 365 天
    cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=365)).strftime("%Y-%m-%d")
    history["pushed"] = [e for e in history["pushed"] if e.get("date", "") >= cutoff]
    _save_question_history(history, ctx)

    _log(f"[reflect.push] 推送问题: {qid} ({cat}) — {question[:30]}")

    return {
        "success": True,
        "reply": f"{emoji} 今天的深度自问\n\n**{question}**\n\n想到什么就说什么，没有标准答案~",
        "state_updates": {
            "reflect_pending": True,
            "reflect_question_id": qid,
            "reflect_question": question,
            "reflect_category": cat,
            "reflect_sent_at": now_str,
        }
    }


def answer(params, state, ctx):
    """
    处理用户对深度自问的回答。
    由 LLM 路由触发（reflect_pending=true 时）。
    """
    if not state.get("reflect_pending"):
        return {"success": False, "reply": "当前没有待回答的深度自问"}

    answer_text = params.get("answer", "").strip()
    if not answer_text:
        return {"success": True, "reply": "回答不能为空哦~"}

    qid = state.get("reflect_question_id", "")
    question = state.get("reflect_question", "")
    category = state.get("reflect_category", "")
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    # 调用 Flash LLM 生成回应
    ai_response = _generate_response(question, category, answer_text, state)

    # 写入 reflect_log
    _write_log_entry({
        "date": today,
        "question_id": qid,
        "category": category,
        "question": question,
        "answer": answer_text,
        "ai_response": ai_response or "",
        "skipped": False,
        "answer_time": datetime.now(BEIJING_TZ).isoformat(),
    }, ctx)

    # 更新统计
    stats = state.get("reflect_stats", {})
    stats["total_answered"] = stats.get("total_answered", 0) + 1
    stats["last_reflect_date"] = today
    cat_counts = stats.get("category_counts", {})
    cat_counts[category] = cat_counts.get(category, 0) + 1
    stats["category_counts"] = cat_counts
    # 连续天数
    last_date = stats.get("_last_answer_date", "")
    if last_date:
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d").date()
            today_dt = datetime.strptime(today, "%Y-%m-%d").date()
            if (today_dt - last_dt).days == 1:
                stats["streak_days"] = stats.get("streak_days", 0) + 1
            elif (today_dt - last_dt).days > 1:
                stats["streak_days"] = 1
        except Exception:
            stats["streak_days"] = 1
    else:
        stats["streak_days"] = 1
    stats["_last_answer_date"] = today

    reply = ai_response or "记下了~"

    _log(f"[reflect.answer] qid={qid}, answer_len={len(answer_text)}")

    return {
        "success": True,
        "reply": reply,
        "state_updates": {
            "reflect_pending": False,
            "reflect_question_id": "",
            "reflect_question": "",
            "reflect_category": "",
            "reflect_sent_at": "",
            "reflect_answer_today": answer_text,
            "reflect_stats": stats,
        }
    }


def skip(params, state, ctx):
    """跳过今日深度自问。"""
    if not state.get("reflect_pending"):
        return {"success": True, "reply": "当前没有待回答的深度自问"}

    qid = state.get("reflect_question_id", "")
    question = state.get("reflect_question", "")
    category = state.get("reflect_category", "")
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    # 记录跳过
    _write_log_entry({
        "date": today,
        "question_id": qid,
        "category": category,
        "question": question,
        "answer": "",
        "ai_response": "",
        "skipped": True,
        "answer_time": datetime.now(BEIJING_TZ).isoformat(),
    }, ctx)

    # 更新统计
    stats = state.get("reflect_stats", {})
    stats["total_skipped"] = stats.get("total_skipped", 0) + 1
    stats["last_reflect_date"] = today
    stats["streak_days"] = 0

    _log(f"[reflect.skip] qid={qid}")

    return {
        "success": True,
        "reply": "没关系，不是每个问题都需要答案。明天见~",
        "state_updates": {
            "reflect_pending": False,
            "reflect_question_id": "",
            "reflect_question": "",
            "reflect_category": "",
            "reflect_sent_at": "",
            "reflect_stats": stats,
        }
    }


def history(params, state, ctx):
    """查看最近的深度自问回答。"""
    days = params.get("days", 7)
    try:
        days = int(days)
    except (ValueError, TypeError):
        days = 7
    days = min(days, 30)

    text = IO.read_text(_reflect_log_file(ctx))
    if not text or not text.strip():
        return {"success": True, "reply": "还没有深度自问的记录呢~"}

    entries = []
    cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("date", "") >= cutoff and not entry.get("skipped"):
                entries.append(entry)
        except Exception:
            pass

    if not entries:
        return {"success": True, "reply": f"最近 {days} 天没有深度自问的回答记录"}

    # 构建回复
    stats = state.get("reflect_stats", {})
    total = stats.get("total_answered", 0)
    streak = stats.get("streak_days", 0)

    parts = [f"📝 最近 {days} 天的深度自问（共回答过 {total} 次，连续 {streak} 天）\n"]
    for entry in entries[-10:]:
        date = entry.get("date", "")
        cat = entry.get("category", "")
        emoji = CATEGORY_EMOJI.get(cat, "💭")
        q = entry.get("question", "")
        a = entry.get("answer", "")
        if len(a) > 60:
            a = a[:60] + "..."
        parts.append(f"{emoji} {date} | {q}\n   → {a}")

    return {
        "success": True,
        "reply": "\n\n".join(parts),
    }


# ============ 辅助函数 ============

def _generate_response(question, category, answer_text, state):
    """调用 Flash LLM 生成对深度自问回答的回应"""
    try:
        from brain import call_llm
        import prompts

        context_parts = [
            f"维度：{category}",
            f"问题：{question}",
            f"用户回答：{answer_text}",
        ]

        mood_scores = state.get("mood_scores", [])
        if mood_scores:
            recent = sorted(mood_scores, key=lambda x: x.get("date", ""), reverse=True)[:3]
            mood_str = ", ".join(f"{s.get('date','')}:{s.get('score','?')}/10" for s in recent)
            context_parts.append(f"近期情绪评分：{mood_str}")

        context = "\n".join(context_parts)

        response = call_llm([
            {"role": "system", "content": prompts.REFLECT_RESPONSE},
            {"role": "user", "content": context}
        ], model_tier="flash", max_tokens=200, temperature=0.7)

        return response
    except Exception as e:
        _log(f"[reflect] AI 回应生成失败: {e}")
        return None


def _write_log_entry(entry, ctx):
    """追加一条记录到 reflect_log.jsonl"""
    try:
        line = json.dumps(entry, ensure_ascii=False)
        path = _reflect_log_file(ctx)
        existing = IO.read_text(path) or ""
        IO.write_text(path, existing + line + "\n")
    except Exception as e:
        _log(f"[reflect] 日志写入失败: {e}")


# Skill 热加载注册表
SKILL_REGISTRY = {
    "reflect.push": push,
    "reflect.answer": answer,
    "reflect.skip": skip,
    "reflect.history": history,
}
