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
import requests
import threading
from datetime import datetime, timezone, timedelta
from config import REFLECT_COOLDOWN_DAYS
from local_io import LocalFileIO as _LocalIO


# ============ 知乎开放平台配置 ============
ZHIHU_APP_ID = "karvis"
ZHIHU_TOKEN = "be779c17f826bd1ba7c675174271c6ba312af07a"
ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"


BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    from logger import log
    log(msg)


def _reflect_dir(ctx):
    """获取用户的 reflect 数据目录（始终本地存储，不走 OneDrive）"""
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

# 维度情感权重（0.1=强负向，1.0=强正向）
# 权重越高 → 选题频率越高；晚间/低情绪时负向维度会被排除
CATEGORY_WEIGHT = {
    "自我认知":     0.6,
    "恐惧与安全感": 0.2,   # 强负向，低频
    "内在对话":     0.4,   # 偏负向，低频
    "人际关系":     0.6,
    "时间与优先级": 0.6,
    "欲望与动力":   0.8,
    "情绪与疗愈":   0.3,   # 负向，低频
    "价值观":       0.6,
    "成长与变化":   0.8,
    "梦想与想象":   0.9,
}

QUESTION_BANK = {
    "自我认知": [
        "用三个词形容自己，你会选什么？",
        "别人眼中的你和你自己认为的你，最大的差别是什么？",
        "你最近发现自己有什么以前没注意到的能力？",
        "你觉得自己最被低估的优点是什么？",
        "如果要给自己写一句墓志铭，你会写什么？",
        "你最像哪个虚构角色？为什么？",
        "你觉得自己“活成了想要的样子”吗？差距在哪？",
        "你习惯用什么方式保护自己？",
        "你最近一次对自己感到骄傲是什么时候？",
        "你觉得自己最矛盾的一面是什么？",
        "如果有一个完全了解你的人，你觉得 TA 会怎样评价你？",
        "你有哪个平时不太展示、但其实挺喜欢的自己的一面？",
        "你最近一次改变了对自己的某个认知是什么时候？",
        "你身上哪个特质是你最想传给孩子的？",
        "如果让今天的你给一个月前的自己一句话，你会说什么？",
        "你觉得自己是一个“真实”的人吗？",
        "如果可以重新设计自己的性格，你会改什么？",
        "你在什么时候最像“真正的自己”？",
        "如果你的某一面可以被更多人看见，你希望是哪一面？",
        "你觉得“了解自己”这件事，你做到了百分之几？",
    ],
    "恐惧与安全感": [
        "你有没有曾经很怕的事，后来发现根本没那么糟？那个转变怎么发生的？",
        "你最怕失去什么？",
        "你最近克服了什么让自己有点紧张的事？感觉怎么样？",
        "什么时候你会感到最不安全？",
        "你有没有一个反复出现的担忧？最近它还在吗？",
        "你觉得面对不确定性，自己的应对方式有什么规律？",
        "如果明天就是世界末日，你今天最想做的一件事是什么？",
        "最近有没有一件事让你觉得“还好我做了这个决定”？",
        "你害怕被遗忘还是害怕被记住？",
        "你觉得自己在什么方面比以前更有底气了？",
        "你习惯在不安全感来临时怎么让自己落地？",
        "有没有一件事，以前觉得做不到，后来做到了？当时是什么给了你力量？",
        "你觉得“害怕”和“谨慎”的区别是什么？你更多是哪种？",
        "你身边有没有一个人，让你觉得“有TA在就不怕”？是什么让你有这种感觉？",
        "你有没有一件一直想做但还没迈出的事？是什么让你犹豫？",
        "你害怕孤独还是害怕不被理解？",
        "如果可以消除一种恐惧，你选哪个？",
        "你觉得安全感来自外在（钱/关系）还是内在（自信/信念）？",
        "你小时候害怕的事情，现在还怕吗？",
        "你觉得自己在什么方面比以前更有把握了？",
    ],
    "内在对话": [
        "你脑子里最常冒出的一句话是什么？",
        "你对自己说话的语气，通常是鼓励的还是批评的？",
        "当你犯错时，内心第一个念头是什么？",
        "你有没有一句话会反复对自己说来安慰自己？",
        "深夜一个人的时候，你的脑海里通常在想什么？",
        "你的内心独白里，最常出现哪个人？",
        "你觉得自己内心有几个“声音”？它们分别在说什么？",
        "当你需要做重大决定时，内心的对话是怎样的？",
        "你有没有一个想法，是你从来没告诉过任何人的？",
        "你最常在什么时候感觉“内心很平静”？",
        "你的内心深处，觉得自己值得被爱吗？",
        "有没有一个“只有自己知道”的小确幸，想到就会微笑？",
        "你有没有一句话，是你一直想说但说不出口的？",
        "你和自己的关系好吗？满分10分你打几分？",
        "你会因为别人的评价而改变对自己的看法吗？",
        "你最近一次真心夸了自己是什么时候？为什么？",
        "你内心最渴望被人说的一句话是什么？",
        "你有没有给自己设过一个“禁区”——不允许自己想的事？",
        "你觉得自己是倾向于内疚还是愤怒？哪种更让你不舒服？",
        "当你说“我没事”的时候，内心真实的感受是什么？",
    ],
    "人际关系": [
        "今天有没有一个瞬间，觉得被某人或某事理解了？",
        "你觉得谁最了解你？TA 了解你到什么程度？",
        "你最近一次感到被深深理解是什么时候？",
        "你有没有人，一想到就觉得“还好有这个人”？",
        "你通常是先付出的那一方，还是等待别人先来的？",
        "你最近发现了什么让你觉得“世界还挺可爱的”？",
        "你觉得自己在关系中最大的模式是什么？",
        "有没有人在你不知道的时候默默帮了你？",
        "你会用什么方式表达爱？你希望别人用什么方式爱你？",
        "你在关系里最欣赏自己的什么特质？",
        "你觉得自己是一个容易信任别人的人吗？",
        "你最感激的一个人是谁？你告诉过 TA 吗？",
        "你和父母的关系，用一个词形容是什么？",
        "你觉得友情中最重要的品质是什么？",
        "你有没有一个人，是你想联系但一直没联系的？",
        "你在群体中通常扮演什么角色？",
        "你觉得自己在某段关系里变得更好了吗？怎么变好的？",
        "你觉得“边界感”对你来说难不难？在哪方面最难？",
        "你最近一次心甘情愿为别人做了什么？",
        "你觉得自己值得拥有好的关系吗？",
    ],
    "时间与优先级": [
        "你最近一件让你觉得“时间花得值”的事是什么？",
        "你最近有没有给自己一段“什么都不用做”的时间？",
        "如果今天是你的最后一天，你想怎么过？",
        "你花最多时间的三件事是什么？它们是你真正重要的事吗？",
        "你有没有一直说“以后再做”的事？",
        "你觉得自己每天最有价值的 1 小时花在了哪里？",
        "如果每天多出 2 小时，你会用来做什么？",
        "你最近做的哪件事，让你有种“活着真好”的感觉？",
        "你觉得自己的时间是“被安排的”还是“自己选择的”？",
        "你上次完全不看手机、沉浸在一件事里是什么时候？",
        "你觉得自己有哪些时间“虽然看起来浪费了，但其实挺值的”？",
        "你觉得“忙”和“充实”的区别是什么？",
        "你每周有多少时间是留给自己的？",
        "你觉得什么事情是“紧急但不重要”的？你能不做吗？",
        "你希望退休后的生活是什么样的？现在能做什么准备？",
        "你花在社交媒体上的时间值得吗？",
        "你有没有一项被你搁置很久的爱好？",
        "你觉得“效率”是不是被过度追求了？",
        "你理想中的一天是什么样的？",
        "你现在的生活节奏，是你想要的吗？",
    ],
    "欲望与动力": [
        "如果不考虑钱和别人的看法，你最想做什么？",
        "你最近一次感到“热血沸腾”是什么时候？",
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
        "你做事的动力更多来自“追求快乐”还是“逃避痛苦”？",
        "你觉得自己的“舒适区”在哪里？你想走出去吗？",
        "什么事情能让你即使很累也愿意去做？",
        "你最近一次说“算了”是在什么情境下？",
        "你觉得“欲望”是好事还是坏事？",
        "你心中有没有一件事，是“如果不做会后悔一辈子”的？",
        "你会为了什么而改变自己的生活方式？",
        "你觉得自己更缺“想清楚”还是“去行动”？",
    ],
    "情绪与疗愈": [
        "你习惯用什么方式让自己好起来？",
        "你觉得什么时候最能充好电？",
        "你通常怎么处理愤怒？压下去还是表达出来？哪种更有效？",
        "最近有没有一件事，你选择了默默扛着？现在还扛着吗？",
        "你觉得自己这阵子在某件事上，比以前更平和了吗？",
        "你最需要被安慰的时刻是什么样的？",
        "你习惯用什么方式让自己好起来？",
        "你有没有一种情绪，是你觉得“不应该有”的？",
        "你最常用什么方式让自己落地？（散步/睡觉/聊天……）",
        "你觉得自己是容易原谅别人的人吗？",
        "你有没有对谁心存愧疚？",
        "当你感觉很累的时候，你需要的是安静还是陪伴？",
        "你有没有一首歌或一段话，是你情绪低落时会想到的？",
        "你觉得“坚强”和“压抑”的界限在哪里？",
        "你有没有把快乐当成义务——觉得自己“应该”开心？",
        "你最后一次允许自己“就是不好”是什么时候？",
        "你觉得悲伤有价值吗？",
        "你有没有一个安全的地方，可以完全放下伪装？",
        "你觉得情绪是需要“管理”的，还是需要“允许”的？",
        "你最近有没有一个让自己感觉“被接住了”的瞬间？",
    ],
    "价值观": [
        "你觉得“成功”对你来说意味着什么？",
        "你最看重的三个人生价值是什么？",
        "你愿意为什么牺牲金钱？为什么牺牲时间？",
        "你觉得什么是“好的生活”？",
        "你人生中最正确的一个决定是什么？",
        "你觉得“自由”和“安全”哪个更重要？",
        "你会因为什么而尊重一个人？",
        "你觉得“公平”真的存在吗？",
        "你人生中有没有一个不可触碰的底线？",
        "你觉得努力和运气哪个更重要？",
        "你会为了什么事情和好朋友翻脸？",
        "你觉得“善良”会不会让人吃亏？",
        "你最不能接受的社会现象是什么？",
        "你觉得人活着的意义是什么？",
        "你愿意用 10 年寿命换取什么？",
        "你觉得“正义”和“规则”冲突的时候应该怎么选？",
        "你人生中最后悔的一次妥协是什么？",
        "你觉得“真实”重要还是“得体”重要？",
        "你对“死亡”的看法是什么？",
        "你觉得什么东西是金钱绝对买不到的？",
    ],
    "成长与变化": [
        "和一年前的自己相比，你最大的变化是什么？",
        "你最近学到的一个教训是什么？",
        "你觉得自己正在变好还是变糟？哪方面？",
        "有什么事情是你以前很在意、现在不在意了的？",
        "你最近一次走出舒适区是什么时候？",
        "你觉得自己成长最快的时期是什么时候？为什么？",
        "你有没有一个曾经的“执念”，现在放下了？",
        "你今天做了一件哪怕很小、但你觉得“做到了”的事是什么？",
        "你最想改掉的一个习惯是什么？",
        "你最近一次改变想法是因为什么？",
        "你觉得自己还有什么潜力没被开发？",
        "你接受自己的不完美吗？哪方面最难接受？",
        "回看过去的自己，你想对 TA 说什么？",
        "你觉得“成熟”意味着什么？",
        "你有没有因为某个人而改变了自己？",
        "你最近克服了什么困难？感受是什么？",
        "你觉得自己还需要学习什么？",
        "你会用什么方式衡量自己的成长？",
        "你觉得“变化”是让你兴奋还是焦虑？",
        "五年后你希望自己是什么样的？",
    ],
    "梦想与想象": [
        "如果能给 10 年后的自己写一封信，你会写什么？",
        "如果可以在任何时代生活，你选哪个？",
        "你最想拥有一种什么超能力？",
        "如果今天的你可以暂停时间，最想在哪个瞬间多停一会儿？",
        "你的人生如果拍成电影，片名叫什么？",
        "如果可以和任何人（古今中外）吃一顿饭，你选谁？",
        "你理想中的退休生活是什么样的？",
        "如果明天醒来发现一切归零，你第一件事做什么？",
        "用一种食物形容这周的自己，会是什么？",
        "如果可以许一个必然实现的愿望，你许什么？",
        "你有没有一个反复出现的白日梦？",
        "如果可以活到 200 岁，你会怎么安排人生？",
        "你最想去但还没去过的地方是哪里？为什么？",
        "如果有一天 AI 可以完全替代你的工作，你会做什么？",
        "你小时候想象的“未来的自己”和现在一样吗？",
        "如果可以向全世界广播一句话，你会说什么？",
        "你觉得“完美的一天”是什么样的？",
        "如果可以拥有另一个人的人生体验一周，你选谁？",
        "如果现在能给5年前的自己发一条微信，你发什么？",
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
    """加载已推送问题历史（本地存储）"""
    path = _question_history_file(ctx)
    text = _LocalIO.read_text(path)
    if text:
        try:
            return json.loads(text)
        except Exception:
            pass
    return {"pushed": []}


def _save_question_history(history, ctx):
    _LocalIO.write_text(_question_history_file(ctx), json.dumps(history, ensure_ascii=False, indent=2))


def _select_question(state, ctx):
    """
    选择今日问题。
    策略：维度权重轮转 → 时段/情绪过滤负向维度 → 维度内随机 → 去重（90天内不重复）
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

    stats = state.get("reflect_stats", {})
    cat_counts = stats.get("category_counts", {})

    # 时段判断：晚上 20:00 后避免推送重度负向题目
    now_hour = datetime.now(BEIJING_TZ).hour
    is_evening = now_hour >= 20

    # 情绪判断：近 3 天均分 ≤ 5 → 回避挖掘类维度
    mood_scores = state.get("mood_scores", [])
    recent_low = False
    if mood_scores:
        recent = sorted(mood_scores, key=lambda x: x.get("date", ""), reverse=True)[:3]
        avg = sum(s.get("score", 5) for s in recent) / len(recent)
        if avg <= 5:
            recent_low = True

    # 动态排除负向维度
    exclude_cats = set()
    if is_evening or recent_low:
        exclude_cats.add("恐惧与安全感")
        exclude_cats.add("情绪与疗愈")
    if recent_low:
        exclude_cats.add("内在对话")

    eligible_cats = [c for c in CATEGORIES if c not in exclude_cats]
    if not eligible_cats:
        eligible_cats = list(CATEGORIES)  # fallback

    # 按（已回答次数 / 情感权重）排序，权重高的维度等效"已回答次数少"
    sorted_cats = sorted(
        eligible_cats,
        key=lambda c: cat_counts.get(c, 0) / CATEGORY_WEIGHT.get(c, 0.5)
    )

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
            _log(f"[reflect._select] cat={cat} weight={CATEGORY_WEIGHT.get(cat,0.5)} exclude={list(exclude_cats)}")
            return {
                "question_id": qid,
                "category": cat,
                "question": question,
            }

    # 所有题目都在冷却期，随机选一个（不受维度限制）
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

    # 异步搜索知乎相关高赞答案并发送给用户
    user_id = ctx.user_id if hasattr(ctx, 'user_id') else ""
    if user_id and question:
        t = threading.Thread(
            target=_async_send_zhihu_answer,
            args=(question, category, user_id),
            daemon=True
        )
        t.start()

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

    text = _LocalIO.read_text(_reflect_log_file(ctx))
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
        existing = _LocalIO.read_text(path) or ""
        _LocalIO.write_text(path, existing + line + "\n")
    except Exception as e:
        _log(f"[reflect] 日志写入失败: {e}")


# ============ 知乎高赞答案搜索 ============

# 最低点赞数阈值，低于此值认为质量不够
_ZHIHU_MIN_VOTEUP = 10


def _search_zhihu_top_answer(question, category):
    """
    从知乎开放平台搜索与深度自问相关的高赞答案。
    策略：优先精确搜索，质量不够时用维度关键词泛化重试。
    """
    import time as _time

    # 第一轮：用问题原文搜索
    search_query = question.strip().rstrip("？?")
    if len(search_query) > 40:
        search_query = search_query[:40]

    result = _do_zhihu_search(search_query)
    if result:
        return result

    # 第二轮：用维度关键词 + 问题核心词泛化搜索
    fallback_query = _build_fallback_query(question, category)
    if fallback_query and fallback_query != search_query:
        _log(f"[reflect.zhihu] 精确搜索质量不足，泛化重试: q={fallback_query}")
        result = _do_zhihu_search(fallback_query)
        if result:
            return result

    _log(f"[reflect.zhihu] 两轮搜索均未找到高质量答案")
    return None


def _build_fallback_query(question, category):
    """构建泛化搜索关键词：从问题中提取核心概念"""
    # 维度→通用高质量关键词映射
    category_keywords = {
        "自我认知": "认识自己 自我探索",
        "恐惧与安全感": "克服恐惧 安全感",
        "内在对话": "内心独白 自我对话",
        "人际关系": "人际关系 相处之道",
        "时间与优先级": "时间管理 人生优先级",
        "欲望与动力": "人生目标 内驱力",
        "情绪与疗愈": "情绪管理 自我疗愈",
        "价值观": "人生价值观 活着的意义",
        "成长与变化": "个人成长 改变自己",
        "梦想与想象": "梦想 理想生活",
    }
    return category_keywords.get(category, "")


def _do_zhihu_search(search_query):
    """
    执行一次知乎搜索并筛选高质量答案。
    返回格式化消息文本或 None。
    """
    import time as _time

    headers = {
        "Authorization": f"Bearer {ZHIHU_TOKEN}",
        "X-Request-Timestamp": str(int(_time.time())),
        "Content-Type": "application/json",
    }

    try:
        _log(f"[reflect.zhihu] 搜索: q={search_query}")
        resp = requests.get(
            ZHIHU_SEARCH_URL,
            params={"Query": search_query},
            headers=headers,
            timeout=10
        )
        _log(f"[reflect.zhihu] API响应: status={resp.status_code}")

        if resp.status_code != 200:
            _log(f"[reflect.zhihu] 搜索失败: HTTP {resp.status_code}, body={resp.text[:200]}")
            return None

        data = resp.json()
        if data.get("Code") != 0:
            _log(f"[reflect.zhihu] API错误: code={data.get('Code')}, msg={data.get('Message')}")
            return None

        items = data.get("Data", {}).get("Items", [])
        _log(f"[reflect.zhihu] 返回 {len(items)} 条结果")

        if not items:
            return None

        # 过滤：排除专栏文章，只保留问答
        filtered = [
            item for item in items
            if "zhuanlan.zhihu.com" not in (item.get("Url") or "")
        ]

        # 过滤：只保留达到最低点赞阈值的答案
        quality = [
            item for item in filtered
            if item.get("VoteUpCount", 0) >= _ZHIHU_MIN_VOTEUP
        ]

        _log(f"[reflect.zhihu] 过滤: 原始={len(items)}, 去专栏={len(filtered)}, "
             f"达标(>={_ZHIHU_MIN_VOTEUP}赞)={len(quality)}")

        if not quality:
            return None

        # 按综合质量评分排序：点赞数 + 评论互动加权
        for item in quality:
            voteup = item.get("VoteUpCount", 0)
            comment = item.get("CommentCount", 0)
            item["_quality_score"] = voteup + comment * 3

        quality_sorted = sorted(quality, key=lambda x: x["_quality_score"], reverse=True)

        # 从 top3 高质量中随机选一条（避免每次都是同一个）
        candidates = quality_sorted[:3]
        top = random.choice(candidates)

        title = top.get("Title", "").replace(" - 知乎", "")
        content = top.get("ContentText", "").strip()
        author = top.get("AuthorName", "匿名用户")
        voteup = top.get("VoteUpCount", 0)
        comment = top.get("CommentCount", 0)
        url = top.get("Url", "")

        _log(f"[reflect.zhihu] 选中: title={title[:30]}, author={author}, "
             f"voteup={voteup}, comment={comment}, score={top['_quality_score']}, "
             f"url={url[:60]}")

        # 截断到合理长度
        if len(content) > 500:
            content = content[:500] + "..."

        if not content:
            _log(f"[reflect.zhihu] 选中条目内容为空, 跳过")
            return None

        # 格式化输出
        result = (
            f"📖 知乎相关回答\n"
            f"「{title}」\n\n"
            f"{content}\n\n"
            f"—— {author}（👍 {voteup}）"
        )
        if url:
            result += f"\n🔗 {url}"

        return result

    except Exception as e:
        _log(f"[reflect.zhihu] 异常: {e}")
        return None


def _async_send_zhihu_answer(question, category, user_id):
    """异步搜索知乎并发送给用户（不阻塞主流程）"""
    import time
    try:
        time.sleep(1.5)  # 等待主回复先送达
        zhihu_answer = _search_zhihu_top_answer(question, category)
        if zhihu_answer:
            import channel_router
            channel_router.send_message(user_id, zhihu_answer)
            _log(f"[reflect.zhihu] 已发送知乎推荐给 {user_id}")
        else:
            _log(f"[reflect.zhihu] 未找到相关知乎内容, user={user_id}")
    except Exception as e:
        _log(f"[reflect.zhihu] 异步发送失败: {e}")


# Skill 热加载注册表
SKILL_REGISTRY = {
    "reflect.push": push,
    "reflect.answer": answer,
    "reflect.skip": skip,
    "reflect.history": history,
}
