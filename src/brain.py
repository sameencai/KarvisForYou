# -*- coding: utf-8 -*-
"""
Karvis 大脑
核心中枢：Prompt 组装 → 多模型路由 → JSON 解析 → Skill 分发 → 记忆更新
"""
import json
import sys
import time as _time
import threading
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL, QWEN_VL_MODEL,
    CHECKIN_TIMEOUT_SECONDS, SCHEDULER_RHYTHM_WINDOW
)
from storage import IO as OneDriveIO  # 统一存储接口
from memory import (
    load_memory,
    format_recent_messages, add_message_to_state, apply_memory_updates,
    read_state_cached, write_state_and_update_cache
)
import prompts

# 复用线程池，减少线程创建开销
_executor = ThreadPoolExecutor(max_workers=6)

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============ LLM 用量日志 ============

# 线程本地变量：暂存当前请求的 user_id，供 LLM 调用层记录用量
_thread_local = threading.local()


def _set_current_user(user_id):
    """设置当前线程的 user_id（在 process() 入口调用）"""
    _thread_local.user_id = user_id


def _log_llm_usage(model_tier, model_name, usage_dict, latency_s):
    """记录一次 LLM 调用的用量到 usage_log.jsonl"""
    try:
        from user_context import USAGE_LOG_FILE, SYSTEM_DIR
        import os

        user_id = getattr(_thread_local, "user_id", "unknown")
        now = datetime.now(timezone(timedelta(hours=8)))

        entry = {
            "ts": now.isoformat(timespec="seconds"),
            "user_id": user_id,
            "model_tier": model_tier,
            "model": model_name,
            "prompt_tokens": usage_dict.get("prompt_tokens", 0),
            "completion_tokens": usage_dict.get("completion_tokens", 0),
            "total_tokens": usage_dict.get("total_tokens", 0),
            "latency_s": round(latency_s, 1),
        }

        os.makedirs(os.path.dirname(USAGE_LOG_FILE), exist_ok=True)
        with open(USAGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log(f"[UsageLog] 记录失败: {e}")


# ============ Skill 注册表 ============

def _get_skill_registry():
    """通过 skill_loader 自动发现并加载所有 skill（O-010）"""
    from skill_loader import load_skill_registry
    return load_skill_registry()


# ============ 多模型 LLM 调用层 ============

def _select_model_tier(payload, is_system_action=False, action=None):
    """
    根据请求类型选择模型层级。
    Returns: "flash" | "main" | "think"
    """
    if is_system_action:
        if action in ("morning_report", "evening_checkin",
                       "daily_report", "weekly_review", "monthly_review"):
            return "main"
        if action == "companion_check":
            return "flash"
        return "main"

    # 用户消息: 走 Main（一次调用完成分类+回复）
    return "main"


def _select_skill_model_tier(skill_name):
    """Skill 执行时的模型选择（Agent Loop 中）"""
    if skill_name in ("deep_dive", "decision_track"):
        return "think"
    return "main"


def call_llm(messages, model_tier="main", max_tokens=500,
             temperature=0.3, enable_thinking=None):
    """
    统一 LLM 调用入口，支持三层模型路由 + 自动降级。
    
    Args:
        model_tier: "flash" | "main" | "think"
        enable_thinking: 覆盖 thinking 设置。None = 按 tier 自动决定
    Returns:
        str: LLM 回复文本，失败返回 None
    """
    try:
        if model_tier == "flash":
            return _call_qwen_flash(messages, max_tokens, temperature)

        thinking = enable_thinking
        if thinking is None:
            thinking = (model_tier == "think")

        return _call_deepseek(messages, max_tokens, temperature,
                              enable_thinking=thinking)
    except Exception as e:
        if model_tier == "flash":
            _log(f"[Brain] Qwen Flash 失败: {e}, 降级到 DeepSeek")
            try:
                return _call_deepseek(messages, max_tokens, temperature,
                                      enable_thinking=False)
            except Exception as e2:
                _log(f"[Brain] DeepSeek 降级也失败: {e2}")
                return None
        _log(f"[Brain] LLM 调用失败 (tier={model_tier}): {e}")
        return None


def _call_deepseek(messages, max_tokens=500, temperature=0.3,
                   enable_thinking=False):
    """调用 DeepSeek V3.2，支持 thinking 模式控制"""
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    # V3.2 支持 thinking 模式控制
    if "v3.2" in DEEPSEEK_MODEL:
        data["enable_thinking"] = enable_thinking

    total_chars = sum(len(m.get("content", "")) for m in messages)
    tier_label = "Think" if enable_thinking else "Main"
    _log(f"[Brain][{tier_label}] DeepSeek请求: model={DEEPSEEK_MODEL}, "
         f"thinking={enable_thinking}, prompt_chars={total_chars}, max_tokens={max_tokens}")

    t0 = _time.time()
    resp = requests.post(url, headers=headers, json=data, timeout=60)
    t1 = _time.time()

    if resp.status_code == 200:
        result = resp.json()
        usage = result.get("usage", {})
        _log(f"[Brain][{tier_label}] DeepSeek响应: {t1-t0:.1f}s, "
             f"prompt_tokens={usage.get('prompt_tokens')}, "
             f"completion_tokens={usage.get('completion_tokens')}")
        _log_llm_usage("think" if enable_thinking else "main",
                       DEEPSEEK_MODEL, usage, t1 - t0)
        return result["choices"][0]["message"]["content"]

    _log(f"[Brain][{tier_label}] DeepSeek API 错误: {resp.status_code} - {resp.text[:200]}")
    raise RuntimeError(f"DeepSeek API {resp.status_code}")


def _call_qwen_flash(messages, max_tokens=500, temperature=0.3):
    """调用 Qwen Flash（阿里云百炼），极快极便宜"""
    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": QWEN_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature
    }

    total_chars = sum(len(m.get("content", "")) for m in messages)
    _log(f"[Brain][Flash] Qwen请求: model={QWEN_MODEL}, "
         f"prompt_chars={total_chars}, max_tokens={max_tokens}")

    t0 = _time.time()
    resp = requests.post(url, headers=headers, json=data, timeout=30)
    t1 = _time.time()

    if resp.status_code == 200:
        result = resp.json()
        usage = result.get("usage", {})
        _log(f"[Brain][Flash] Qwen响应: {t1-t0:.1f}s, "
             f"prompt_tokens={usage.get('prompt_tokens')}, "
             f"completion_tokens={usage.get('completion_tokens')}")
        _log_llm_usage("flash", QWEN_MODEL, usage, t1 - t0)
        return result["choices"][0]["message"]["content"]

    _log(f"[Brain][Flash] Qwen API 错误: {resp.status_code} - {resp.text[:200]}")
    raise RuntimeError(f"Qwen API {resp.status_code}")


def _call_qwen_vl(image_base64, prompt=None):
    """
    调用千问 VL（视觉语言模型）理解图片内容。
    
    Args:
        image_base64: 图片的 base64 编码字符串
        prompt: 图片理解的提示语
    Returns:
        str: 图片描述文本，失败返回 None
    """
    if prompt is None:
        prompt = prompts.VL_DEFAULT
    url = f"{QWEN_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": QWEN_VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "max_tokens": 500
    }

    _log(f"[Brain][VL] Qwen VL请求: model={QWEN_VL_MODEL}, "
         f"image_size={len(image_base64)//1024}KB, prompt={prompt[:50]}")

    t0 = _time.time()
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        t1 = _time.time()

        if resp.status_code == 200:
            result = resp.json()
            usage = result.get("usage", {})
            description = result["choices"][0]["message"]["content"]
            _log(f"[Brain][VL] Qwen VL响应: {t1-t0:.1f}s, "
                 f"prompt_tokens={usage.get('prompt_tokens')}, "
                 f"completion_tokens={usage.get('completion_tokens')}, "
                 f"desc={description[:80]}")
            _log_llm_usage("vl", QWEN_VL_MODEL, usage, t1 - t0)
            return description

        _log(f"[Brain][VL] Qwen VL API 错误: {resp.status_code} - {resp.text[:200]}")
        return None
    except Exception as e:
        _log(f"[Brain][VL] Qwen VL 调用异常: {e}")
        return None


# 向后兼容：保留 call_deepseek 别名
def call_deepseek(messages, max_tokens=500, temperature=0.3):
    """向后兼容：等同于 call_llm(tier='main', thinking=off)"""
    return call_llm(messages, model_tier="main", max_tokens=max_tokens,
                    temperature=temperature)


# ============ Prompt 组装 ============

def build_system_prompt(state, ctx, prompt_futs=None):
    """组装完整的 System Prompt（多用户版，支持用户自定义 SOUL）
    
    prompt_futs: 可选，外部提前提交的 {"mem": Future} dict，用于与 state 读取并行
    """
    beijing_tz = timezone(timedelta(hours=8))
    current_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M %A")

    # memory 从该用户的文件加载
    if prompt_futs and "mem" in prompt_futs:
        mem = prompt_futs["mem"].result()
    else:
        mem = load_memory(ctx)

    recent = format_recent_messages(state)
    state_summary = _build_state_summary(state)

    # SOUL 支持用户自定义覆写
    soul = prompts.SOUL
    soul_override = ctx.get_soul_override()
    if soul_override:
        soul += f"\n\n## 用户自定义\n{soul_override}"
    nickname = ctx.get_nickname()
    if nickname:
        soul += f"\n- 称呼用户为「{nickname}」"

    return f"""{soul}

## 长期记忆
{mem}

## 最近对话
{recent}

## 当前状态
{state_summary}

## 当前时间
{current_time}

{prompts.SKILLS}

{prompts.RULES}

{prompts.OUTPUT_FORMAT}"""


def _build_state_summary(state):
    """从 state 中提取关键信息，构建给 LLM 看的摘要"""
    parts = []

    # 打卡状态
    if state.get("checkin_pending"):
        step = state.get("checkin_step", 0)
        questions = [
            "今天做了什么？",
            "今天状态打几分？(1-10)",
            "什么事让你纠结？",
            "脑子里最常冒出的念头是什么？"
        ]
        q = questions[step - 1] if 1 <= step <= 4 else "未知"
        parts.append(f"打卡进行中: 第 {step}/4 题, 当前问题: \"{q}\"")
        answers = state.get("checkin_answers", [])
        if answers:
            parts.append(f"已回答 {len(answers)} 题")
    else:
        parts.append("未在打卡")

    # 活跃书籍/影视
    active_book = state.get("active_book", "")
    if active_book:
        parts.append(f"正在读: 《{active_book}》")

    active_media = state.get("active_media", "")
    if active_media:
        parts.append(f"正在看: 《{active_media}》")

    # V3-F12: 每日 Top 3
    daily_top3 = state.get("daily_top3", {})
    if daily_top3 and daily_top3.get("items"):
        beijing_tz = timezone(timedelta(hours=8))
        today_str = datetime.now(beijing_tz).strftime("%Y-%m-%d")
        top3_date = daily_top3.get("date", "")
        items = daily_top3["items"]
        items_str = " / ".join(
            f"{'✅' if i.get('done') else '⬜'} {i.get('text', '')}"
            for i in items
        )
        if top3_date == today_str:
            parts.append(f"今日 Top 3: {items_str}")
        else:
            parts.append(f"昨日({top3_date}) Top 3: {items_str}")

    # V3-F11: 活跃实验
    exp = state.get("active_experiment")
    if exp and exp.get("status") == "active":
        tracking = exp.get("tracking", {})
        triggers_str = "、".join(exp.get("triggers", [])[:3]) if exp.get("triggers") else ""
        parts.append(
            f"活跃实验: 「{exp.get('name', '')}」"
            f"(触发词: {triggers_str}, "
            f"触发{tracking.get('trigger_count', 0)}次/"
            f"接受{tracking.get('accepted_count', 0)}次)"
        )

    # V3-F15: 待复盘决策
    pending_decisions = state.get("pending_decisions", [])
    unreviewed = [d for d in pending_decisions if not d.get("result")]
    if unreviewed:
        beijing_tz = timezone(timedelta(hours=8))
        today_str = datetime.now(beijing_tz).strftime("%Y-%m-%d")
        due = [d for d in unreviewed if d.get("review_date", "9999") <= today_str]
        if due:
            topics = "、".join(f"「{d.get('topic', '')}」" for d in due[:3])
            parts.append(f"到期待复盘决策: {topics}")
        elif len(unreviewed) <= 3:
            topics = "、".join(f"「{d.get('topic', '')}」" for d in unreviewed)
            parts.append(f"待复盘决策({len(unreviewed)}): {topics}")
        else:
            parts.append(f"待复盘决策: {len(unreviewed)} 个")

    return "\n".join(parts) if parts else "无特殊状态"


# ============ 核心处理流程 ============

def process(payload, send_fn=None, ctx=None):
    """
    Karvis 大脑的核心入口（多用户版）。

    参数:
        payload: dict, 结构化消息
        send_fn: 回复回调
        ctx: UserContext, 当前用户上下文
    """
    t_start = _time.time()
    _log(f"[Brain] 收到: {json.dumps(payload, ensure_ascii=False)[:200]}")

    # 设置当前线程的 user_id，供 LLM 用量日志使用
    user_id = payload.get("user_id", "unknown")
    _set_current_user(user_id)

    # 0. 预热 OneDrive token + Graph API 连接（串行，一举两得）
    #    预热读取会建立到 graph.microsoft.com 的 TLS 连接，后续请求复用
    OneDriveIO.get_token()
    t_token = _time.time()
    _log(f"[Brain][耗时] token预热: {t_token - t_start:.1f}s")

    # 1. 读取 state 和 memory（并发，按用户隔离）
    state_future = _executor.submit(read_state_cached, ctx)
    prompt_futs = {
        "mem": _executor.submit(load_memory, ctx),
    }

    # 2. 先提取 user_text（不依赖 state 和 prompt，CPU 操作）
    #    图片消息：如果带有 base64 数据，先调 VL 模型获取描述
    if payload.get("type") == "image" and payload.get("image_base64"):
        _log("[Brain] 检测到图片，调用千问 VL 进行图像理解...")
        vl_desc = _call_qwen_vl(payload["image_base64"])
        if vl_desc:
            payload["image_description"] = vl_desc
            _log(f"[Brain] 图像理解完成: {vl_desc[:100]}")
        else:
            _log("[Brain] 图像理解失败，降级为普通图片处理")
        # 释放 base64 数据，节省内存
        del payload["image_base64"]

    user_text = _extract_user_text(payload)

    # 等 state 结果（可能命中 /tmp 缓存，<1ms）
    state = state_future.result() or {}
    t_state = _time.time()
    _log(f"[Brain][耗时] state读取: {t_state - t_token:.1f}s")

    # 3. 检查打卡超时
    _check_checkin_timeout(state)

    # 4. 记录用户消息到短期记忆 + 更新 nudge_state（F5）
    if user_text and payload.get("type") != "system":
        add_message_to_state(state, "user", user_text)
        _update_nudge_state(state)

    # 5. 构建 prompt 并调用 LLM（prompt_futs 在步骤 1 已提交，此处直接取结果）
    system_prompt = build_system_prompt(state, ctx, prompt_futs=prompt_futs)
    t_prompt = _time.time()
    _log(f"[Brain][耗时] prompt组装: {t_prompt - t_state:.1f}s (prompt长度={len(system_prompt)})")

    user_message = _build_user_message(payload)

    # 多模型路由：根据请求类型选择模型层级
    is_system = payload.get("type") == "system"
    action = payload.get("action", "") if is_system else None
    model_tier = _select_model_tier(payload, is_system_action=is_system, action=action)
    _log(f"[Brain] 模型路由: tier={model_tier}, is_system={is_system}, action={action}")

    llm_response = call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ], model_tier=model_tier)
    t_llm = _time.time()
    _log(f"[Brain][耗时] LLM调用({model_tier}): {t_llm - t_prompt:.1f}s")

    if not llm_response:
        _log("[Brain] LLM 返回空，降级处理")
        # Quick-Notes 统一写入
        if payload.get("type") != "system":
            _save_to_quick_notes(payload, state, ctx)
        return {"reply": "已记录到 Obsidian（AI 暂时不可用）"}

    # 6. 解析 LLM 输出
    decision = _parse_llm_output(llm_response)
    if not decision:
        _log(f"[Brain] JSON 解析失败，原始: {llm_response[:300]}")
        if payload.get("type") != "system":
            _save_to_quick_notes(payload, state, ctx)
        return {"reply": "已记录到 Obsidian"}

    _log(f"[Brain] 决策: skill={decision.get('skill')}, thinking={decision.get('thinking', '')[:80]}")
    if decision.get("memory_updates"):
        _log(f"[Brain] 记忆更新: {json.dumps(decision['memory_updates'], ensure_ascii=False)[:200]}")

    registry = _get_skill_registry()

    # 7. 所有用户消息统一写入 Quick-Notes（原始流水记录）
    #    system 类型、打卡回答除外
    primary_skill = _get_primary_skill(decision)
    if payload.get("type") != "system" and primary_skill not in ("checkin.answer", "checkin.skip", "checkin.cancel", "checkin.start"):
        _save_to_quick_notes(payload, state, ctx)

    # 8. 执行 Steps（支持单步旧格式 + 多步 steps 格式）
    steps, step_results = _execute_steps(decision, state, registry, ctx)
    t_skill = _time.time()
    _log(f"[Brain][耗时] Skill执行: {t_skill - t_llm:.1f}s")

    # V3-F10: Agent Loop — 如果 LLM 返回 continue=true 且 skill 返回 agent_context，进入多轮循环
    if len(steps) == 1 and decision.get("continue"):
        first_result = step_results[0]["result"] if step_results else {}
        agent_context = first_result.get("agent_context") if isinstance(first_result, dict) else None
        first_skill = steps[0].get("skill", "")
        if agent_context and first_skill.startswith("internal."):
            decision, last_skill_result = _run_agent_loop(
                system_prompt, user_message, decision, agent_context, state, registry, ctx
            )
            steps = [{"skill": decision.get("skill", "ignore"), "params": decision.get("params", {})}]
            step_results = [{"skill": decision.get("skill", "ignore"), "result": last_skill_result or {"success": True}}]
            t_agent = _time.time()
            _log(f"[Brain][耗时] Agent Loop: {t_agent - t_skill:.1f}s")
            t_skill = t_agent

    # 9. 合并状态更新（从所有 step 结果中收集）
    for sr in step_results:
        r = sr.get("result", {})
        if isinstance(r, dict):
            if r.get("state_updates"):
                _log(f"[Brain] 合并 state_updates from {sr.get('skill')}: {list(r['state_updates'].keys())}")
                state.update(r["state_updates"])
            # 合并 skill handler 返回的 memory_updates（如 settings 设置）
            if r.get("memory_updates"):
                existing = decision.get("memory_updates", [])
                decision["memory_updates"] = existing + r["memory_updates"]
                _log(f"[Brain] 合并 memory_updates from {sr.get('skill')}: "
                     f"新增{len(r['memory_updates'])}条, 总计{len(decision['memory_updates'])}条")
    llm_state_updates = decision.get("state_updates", {})
    if llm_state_updates:
        state.update(llm_state_updates)

    # 10. 智能回复路由：简单 skill 直接用 decision.reply，复杂场景走 Flash 二次加工
    reply = _resolve_reply(user_text, decision, steps, step_results)
    _log(f"[Brain] 回复路由: reply={'有' if reply else '无'}({len(reply) if reply else 0}字)")

    # 兜底：用户消息必须有回复（system 类型除外）
    if not reply and payload.get("type") != "system":
        if decision.get("memory_updates"):
            reply = "记住啦~"
            _log(f"[Brain] 兜底回复: 有memory_updates → '记住啦~'")
        elif primary_skill == "note.save":
            reply = "已记录 ✅"
        elif primary_skill == "ignore":
            reply = "收到~"
        else:
            reply = "好的~"
            _log(f"[Brain] 兜底回复: skill={primary_skill} → '{reply}'")

    if reply:
        add_message_to_state(state, "karvis", reply)

    # 10. 先发回复（O-001：用户感知延迟优化），再保存 state/memory
    if send_fn and reply:
        try:
            send_fn(reply)
            _log(f"[Brain] 回复已先行发送，开始后台保存")
        except Exception as e:
            _log(f"[Brain] 先行发送失败: {e}")

    # V8: 更新用户节奏画像（纯数据收集，不影响回复）
    try:
        _update_user_rhythm(state)
    except Exception as e:
        _log(f"[Brain][V8] 节奏更新失败（不影响主流程）: {e}")

    t_save_start = _time.time()
    _save_state_and_memory(state, decision, payload=payload, reply=reply, elapsed=t_save_start - t_start, ctx=ctx)
    t_end = _time.time()
    _log(f"[Brain][耗时] 保存state: {t_end - t_save_start:.1f}s | 总计: {t_end - t_start:.1f}s")

    return {"reply": reply, "already_sent": bool(send_fn and reply)}


def _save_state_and_memory(state, decision, payload=None, reply=None, elapsed=None, ctx=None):
    """保存 state、更新记忆、写决策日志（并发写，但同步等完成）"""
    futs = []
    futs.append(_executor.submit(_write_state, state, ctx))

    memory_updates = decision.get("memory_updates", [])
    if memory_updates:
        _log(f"[Brain] 异步保存 memory_updates: {len(memory_updates)} 条")
        futs.append(_executor.submit(_write_memory, memory_updates, ctx))

    # 决策日志
    futs.append(_executor.submit(_write_decision_log, payload, decision, reply, elapsed, ctx))

    # 等全部写完再返回，确保 SCF 不会冻结中途
    for f in futs:
        try:
            f.result(timeout=30)
        except Exception as e:
            _log(f"[Brain] 写入异常: {e}")


def _write_state(state, ctx):
    try:
        write_state_and_update_cache(state, ctx)
    except Exception as e:
        _log(f"[Brain] state 保存失败: {e}")


def _write_memory(memory_updates, ctx):
    try:
        apply_memory_updates(memory_updates, ctx)
    except Exception as e:
        _log(f"[Brain] 记忆更新失败: {e}")


def _write_decision_log(payload, decision, reply, elapsed, ctx):
    """将每次决策写入 JSONL 日志（追加模式）"""
    try:
        beijing_tz = timezone(timedelta(hours=8))
        now_str = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")

        input_type = payload.get("type", "") if payload else ""
        input_text = ""
        if input_type == "text":
            input_text = payload.get("text", "")[:100]
        elif input_type == "voice":
            input_text = payload.get("text", "")[:100]
        elif input_type == "system":
            input_text = payload.get("action", "")

        entry = {
            "ts": now_str,
            "user_id": ctx.user_id if ctx else "",
            "input_type": input_type,
            "input": input_text,
            "thinking": decision.get("thinking", "")[:100] if decision else "",
            "skill": decision.get("skill", "") if decision else "",
            "reply": (reply or "")[:100],
            "has_memory_updates": bool(decision.get("memory_updates")) if decision else False,
            "elapsed_s": round(elapsed, 1) if elapsed else None,
        }
        line = json.dumps(entry, ensure_ascii=False)

        log_file = ctx.decision_log_file if ctx else ""
        if log_file:
            existing = OneDriveIO.read_text(log_file) or ""
            new_content = existing + line + "\n"
            OneDriveIO.write_text(log_file, new_content)
        _log(f"[Brain] 决策日志已写入: skill={entry['skill']}")
    except Exception as e:
        _log(f"[Brain] 决策日志写入失败（不影响主流程）: {e}")


# ============ V3-F10: Agent Loop ============

def _run_agent_loop(system_prompt, user_message, first_decision, first_context, state, registry, ctx):
    """
    多轮 Agent Loop：LLM 可以连续调用 internal.* skill 获取更多信息，
    直到返回 continue=false 或达到最大轮数。

    返回: (final_decision, final_skill_result)
    """
    MAX_ROUNDS = 5
    ROUND_TIMEOUT = 30

    # 构建对话历史
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
        # 第一轮 LLM 的回复
        {"role": "assistant", "content": json.dumps(first_decision, ensure_ascii=False)},
        # 第一轮 skill 的执行结果
        {"role": "user", "content": json.dumps({
            "type": "agent_step",
            "step": 1,
            "skill_result": first_context
        }, ensure_ascii=False)}
    ]

    last_decision = first_decision
    last_skill_result = {"success": True, "agent_context": first_context}

    for step in range(2, MAX_ROUNDS + 1):
        _log(f"[Brain][AgentLoop] 第 {step} 轮")

        # Agent Loop 中走 Main（后续可按 skill 动态选择 tier）
        llm_response = call_llm(messages, model_tier="main", max_tokens=500, temperature=0.3)
        if not llm_response:
            _log(f"[Brain][AgentLoop] LLM 返回空，终止循环")
            break

        decision = _parse_llm_output(llm_response)
        if not decision:
            _log(f"[Brain][AgentLoop] JSON 解析失败，终止循环")
            break

        last_decision = decision
        skill_name = decision.get("skill", "ignore")
        params = decision.get("params", {})

        _log(f"[Brain][AgentLoop] step={step}, skill={skill_name}, continue={decision.get('continue')}")

        # 如果不再继续，退出循环
        if not decision.get("continue"):
            # 如果最后一步有非 internal skill，执行它
            if skill_name and not skill_name.startswith("internal.") and skill_name != "ignore":
                handler = registry.get(skill_name)
                if handler:
                    try:
                        last_skill_result = handler(params, state, ctx)
                    except Exception as e:
                        _log(f"[Brain][AgentLoop] 最终 Skill {skill_name} 执行失败: {e}")
                        last_skill_result = {"success": False}
            break

        # 继续循环：执行 internal.* skill
        handler = registry.get(skill_name)
        if not handler:
            _log(f"[Brain][AgentLoop] 未知 skill: {skill_name}，终止")
            break

        try:
            skill_result = handler(params, state, ctx)
            agent_context = skill_result.get("agent_context") if isinstance(skill_result, dict) else None
        except Exception as e:
            _log(f"[Brain][AgentLoop] Skill {skill_name} 异常: {e}")
            agent_context = {"error": str(e)}

        last_skill_result = skill_result or {"success": True}

        # 追加到对话历史
        messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        messages.append({"role": "user", "content": json.dumps({
            "type": "agent_step",
            "step": step,
            "skill_result": agent_context or {}
        }, ensure_ascii=False)})

    _log(f"[Brain][AgentLoop] 循环结束，最终 skill={last_decision.get('skill')}")
    return last_decision, last_skill_result


# ============ 辅助函数 ============

# ── V4: Flash 回复层 — prompt 从 prompts 模块取 ──

# ── V4: 不需要 Flash 加工的简单 skill ──
_SIMPLE_SKILLS = frozenset({
    "note.save", "classify.archive", "todo.add", "todo.done",
    "checkin.start", "checkin.answer", "checkin.skip", "checkin.cancel",
    "book.create", "book.excerpt", "book.thought", "book.summary", "book.quotes",
    "media.create", "media.thought",
    "mood.generate", "voice.journal",
    "settings.nickname", "settings.soul", "settings.info",
    "web.token",
    "habit.propose", "habit.nudge", "habit.status", "habit.complete",
    "decision.record", "dynamic",
})


def _get_primary_skill(decision):
    """从 decision 中提取主 skill 名称（兼容 steps 和旧格式）"""
    steps = decision.get("steps")
    if steps and len(steps) > 0:
        return steps[0].get("skill", "ignore")
    return decision.get("skill", "ignore")


def _execute_steps(decision, state, registry, ctx):
    """
    V4: 执行 steps 数组中的所有 skill，收集结果。
    兼容旧格式（单 skill + params）。
    """
    steps = decision.get("steps")
    if not steps:
        skill = decision.get("skill", "ignore")
        params = decision.get("params", {})
        steps = [{"skill": skill, "params": params}]

    results = []
    for i, step in enumerate(steps):
        skill_name = step.get("skill", "ignore")
        params = step.get("params", {})
        handler = registry.get(skill_name)

        if skill_name == "note.save":
            _log(f"[Brain] Step {i}: note.save 已由统一写入处理，跳过")
            results.append({"skill": skill_name, "result": {"success": True}})
            continue
        if skill_name == "ignore":
            results.append({"skill": skill_name, "result": {"success": True}})
            continue
        if not handler:
            _log(f"[Brain] Step {i}: 未知 skill {skill_name}")
            results.append({"skill": skill_name, "result": {"success": False, "error": f"未知 skill: {skill_name}"}})
            continue

        try:
            result = handler(params, state, ctx)
            results.append({"skill": skill_name, "result": result or {"success": True}})
            _log(f"[Brain] Step {i}: {skill_name} → success={result.get('success') if isinstance(result, dict) else True}")
        except Exception as e:
            _log(f"[Brain] Step {i}: {skill_name} 异常: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            results.append({"skill": skill_name, "result": {"success": False, "error": str(e)}})

    return steps, results


def _resolve_reply(user_text, decision, steps, step_results):
    """
    V4: 智能回复路由。
    简单 skill → 直接用 decision.reply 或 skill.reply
    复杂场景 → Flash 二次加工
    """
    all_skills = [s.get("skill", "ignore") for s in steps]
    llm_reply = decision.get("reply")

    # 快速路径 1：ignore（纯闲聊），LLM 的 reply 就是最终回复
    if all_skills == ["ignore"] and llm_reply:
        return llm_reply

    # 快速路径 2：所有 step 都是简单 skill
    if all(s in _SIMPLE_SKILLS for s in all_skills):
        # 优先用 skill 返回的 reply，其次用 LLM 预生成的 reply
        for sr in step_results:
            r = sr.get("result", {})
            if isinstance(r, dict) and r.get("reply"):
                return r["reply"]
        return llm_reply

    # 快速路径 3：单步且有 skill_reply 的简单 skill
    if len(step_results) == 1 and all_skills[0] in _SIMPLE_SKILLS:
        r = step_results[0].get("result", {})
        return r.get("reply") if isinstance(r, dict) else llm_reply

    # 复杂路径：需要 Flash 二次加工
    _log(f"[Brain][V4] 触发 Flash 回复层: skills={all_skills}")
    t0 = _time.time()
    flash_reply = _call_flash_for_reply(user_text, decision, steps, step_results)
    t1 = _time.time()
    _log(f"[Brain][V4][耗时] Flash回复生成: {t1-t0:.1f}s")
    return flash_reply or llm_reply


def _call_flash_for_reply(user_text, decision, steps, step_results):
    """V4: 调用 Flash 模型，基于用户意图 + skill 执行结果生成最终回复"""
    context_parts = []
    context_parts.append(f"用户消息: {user_text}")
    context_parts.append(f"AI 判断: {decision.get('thinking', '')}")

    for i, (step, sr) in enumerate(zip(steps, step_results)):
        skill_name = step.get("skill", "")
        r = sr.get("result", {})
        if not isinstance(r, dict):
            r = {"success": True}
        success = r.get("success", False)
        reply_data = r.get("reply", "")
        error = r.get("error", "")

        if success and reply_data:
            context_parts.append(f"操作{i+1} [{skill_name}] 成功，数据:\n{reply_data}")
        elif success:
            context_parts.append(f"操作{i+1} [{skill_name}] 成功")
        else:
            context_parts.append(f"操作{i+1} [{skill_name}] 失败: {error or reply_data}")

    llm_reply = decision.get("reply", "")
    if llm_reply:
        context_parts.append(f"AI 预生成回复（仅供参考）: {llm_reply}")

    context = "\n".join(context_parts)

    try:
        reply = call_llm([
            {"role": "system", "content": prompts.FLASH_REPLY},
            {"role": "user", "content": context}
        ], model_tier="flash", max_tokens=300, temperature=0.5)
        return reply
    except Exception as e:
        _log(f"[Brain][V4] Flash 回复生成失败: {e}")
        return None

def _save_to_quick_notes(payload, state, ctx):
    """所有用户消息统一写入 Quick-Notes（原始流水记录）"""
    try:
        from skills import note_save
        content = ""
        attachment = ""
        msg_type = payload.get("type", "")

        if msg_type == "text":
            content = payload.get("text", "")
        elif msg_type == "voice":
            content = payload.get("text", "")
            attachment = payload.get("attachment", "")
        elif msg_type == "image":
            attachment = payload.get("attachment", "")
        elif msg_type == "video":
            attachment = payload.get("attachment", "")
        elif msg_type == "link":
            title = payload.get("title", "")
            url = payload.get("url", "")
            desc = payload.get("description", "")
            content = f"[{title}]({url})" if url else title
            if desc:
                content += f"\n\n> {desc}"

        if content or attachment:
            note_save.execute({"content": content, "attachment": attachment}, state, ctx)
    except Exception as e:
        _log(f"[Brain] Quick-Notes 统一写入失败（不影响主流程）: {e}")

def _extract_user_text(payload):
    """从 payload 中提取用户文本（用于短期记忆）"""
    msg_type = payload.get("type", "")
    if msg_type == "text":
        return payload.get("text", "")
    elif msg_type == "voice":
        return f"[语音] {payload.get('text', '')}"
    elif msg_type == "image":
        # 如果有图片描述，记录到短期记忆
        desc = payload.get("image_description", "")
        return f"[图片] {desc}" if desc else "[图片]"
    elif msg_type == "video":
        return "[视频]"
    elif msg_type == "link":
        return f"[链接] {payload.get('title', '')}"
    return ""


def _build_user_message(payload):
    """构建发给 LLM 的 user message"""
    msg_type = payload.get("type", "")

    if msg_type == "text":
        data = {"type": "text", "text": payload.get("text", "")}
        # F1: 如果检测到 URL 并抓取了正文，传给 LLM
        page_content = payload.get("page_content", "")
        if page_content:
            data["page_content"] = page_content
            detected_url = payload.get("detected_url", "")
            if detected_url:
                data["detected_url"] = detected_url
        return json.dumps(data, ensure_ascii=False)

    elif msg_type == "voice":
        asr_text = payload.get("text", "")
        return json.dumps({
            "type": "voice",
            "asr_text": asr_text,
            "text_length": len(asr_text),
            "attachment": payload.get("attachment", "")
        }, ensure_ascii=False)

    elif msg_type == "image":
        data = {
            "type": "image",
            "attachment": payload.get("attachment", "")
        }
        # 图片理解：如果有 VL 描述，传给 LLM
        image_desc = payload.get("image_description", "")
        if image_desc:
            data["image_description"] = image_desc
        return json.dumps(data, ensure_ascii=False)

    elif msg_type == "video":
        return json.dumps({
            "type": "video",
            "attachment": payload.get("attachment", "")
        }, ensure_ascii=False)

    elif msg_type == "link":
        data = {
            "type": "link",
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "description": payload.get("description", "")
        }
        # F1: 如果有抓取到的网页正文，传给 LLM
        page_content = payload.get("content", "")
        if page_content:
            data["page_content"] = page_content
        return json.dumps(data, ensure_ascii=False)

    elif msg_type == "system":
        msg = {
            "type": "system",
            "action": payload.get("action", "")
        }
        # 注入上下文数据（O-007：待办、速记等）
        context = payload.get("context", {})
        if context:
            msg["context"] = context
        return json.dumps(msg, ensure_ascii=False)

    return json.dumps(payload, ensure_ascii=False)


def _parse_llm_output(text):
    """解析 LLM 输出的 JSON（容错处理）"""
    text = text.strip()

    # 剥离 thinking 模式的 <think>...</think> 标签（防御性处理）
    if "<think>" in text:
        think_end = text.find("</think>")
        if think_end >= 0:
            text = text[think_end + len("</think>"):].strip()
        else:
            text = text.replace("<think>", "").strip()

    # 去除 markdown 代码块标记
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首行和末行
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 块
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    _log(f"[Brain] 无法解析 JSON: {text[:200]}")
    return None


def _update_nudge_state(state):
    """F5: 每次收到用户消息时更新 nudge_state（连续记录天数 + 精确时间）"""
    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz)
    today_str = now.strftime("%Y-%m-%d")

    nudge = state.setdefault("nudge_state", {
        "streak": 0,
        "last_message_date": "",
        "last_message_time": "",
        "last_companion_time": "",
        "companion_count_today": 0,
        "yesterday_mood_score": None,
        "people_last_mentioned": {}
    })

    # 精确到分钟的最后消息时间（companion_check 防骚扰用）
    nudge["last_message_time"] = now.strftime("%Y-%m-%d %H:%M")

    last_date = nudge.get("last_message_date", "")
    if last_date != today_str:
        # 新的一天第一条消息：重置每日计数器
        nudge["companion_count_today"] = 0
        nudge["mood_followed_today"] = False

        if last_date:
            try:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d").date()
                today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
                if (today_dt - last_dt).days == 1:
                    nudge["streak"] = nudge.get("streak", 0) + 1
                elif (today_dt - last_dt).days > 1:
                    nudge["streak"] = 1
            except Exception:
                nudge["streak"] = 1
        else:
            nudge["streak"] = 1
        nudge["last_message_date"] = today_str


def _check_checkin_timeout(state):
    """检查打卡是否超时"""
    if not state.get("checkin_pending"):
        return

    sent_at = state.get("checkin_sent_at", "")
    if not sent_at:
        return

    try:
        beijing_tz = timezone(timedelta(hours=8))
        now = datetime.now(beijing_tz)
        sent_time = datetime.strptime(sent_at, "%Y-%m-%d %H:%M")
        sent_time = sent_time.replace(tzinfo=beijing_tz)
        diff = (now - sent_time).total_seconds()
        if diff > CHECKIN_TIMEOUT_SECONDS:
            _log(f"[Brain] 打卡超时 ({diff:.0f}s)")
            from skills import checkin_flow
            checkin_flow.finish(state, timeout=True)
    except Exception as e:
        _log(f"[Brain] 打卡超时检查异常: {e}")


# ============ V8: 用户节奏学习 ============

def _update_user_rhythm(state):
    """V8: 从用户行为中学习作息节奏（每次消息后调用）

    收集数据：
    - 每小时活跃计数（hour_counts）
    - 每日首条消息时间 → 滑动平均 avg_wake_time
    - 每日末条消息时间 → 次日回看更新 avg_sleep_time
    """
    beijing_tz = timezone(timedelta(hours=8))
    now = datetime.now(beijing_tz)
    hour = now.hour
    today_str = now.strftime("%Y-%m-%d")

    sched = state.setdefault("scheduler", {})
    rhythm = sched.setdefault("user_rhythm", {})

    # 1. 更新活跃时段统计
    hour_counts = rhythm.setdefault("hour_counts", {})
    hour_str = str(hour)
    hour_counts[hour_str] = hour_counts.get(hour_str, 0) + 1

    # 2. 推算起床时间：今天第一条消息的时间
    if rhythm.get("_last_wake_date") != today_str:
        # 新一天的第一条消息 → 记录为今天的起床时间
        # 先回看昨天的入睡时间
        last_active = rhythm.get("_last_active_time")
        last_active_date = rhythm.get("_last_active_date")
        if last_active and last_active_date and last_active_date != today_str:
            # 入睡时间只接受 20:00~04:00（次日凌晨），过滤异常值
            try:
                la_parts = last_active.split(":")
                la_min = int(la_parts[0]) * 60 + int(la_parts[1])
                if la_min >= 1200 or la_min < 240:  # 20:00+ 或 <04:00
                    _update_avg_time(rhythm, "avg_sleep_time", last_active,
                                     window=SCHEDULER_RHYTHM_WINDOW)
            except (ValueError, IndexError):
                pass

        rhythm["_last_wake_date"] = today_str

        # 起床时间只接受 05:00~12:00 的首条消息，过滤下午/晚上的异常值
        if 5 <= hour <= 11:
            rhythm["_today_wake"] = now.strftime("%H:%M")
            _update_avg_time(rhythm, "avg_wake_time", now.strftime("%H:%M"),
                             window=SCHEDULER_RHYTHM_WINDOW)
        else:
            _log(f"[Brain][V8] 今日首条消息在 {hour}:xx，不更新 wake_time")

        # 周末偏移统计（同样只在合理时间窗口内更新）
        if now.weekday() >= 5 and 5 <= hour <= 13:
            _update_weekend_shift(rhythm, now.strftime("%H:%M"))

    # 3. 记录最后活跃时间（下次新一天时用于推算入睡）
    rhythm["_last_active_time"] = now.strftime("%H:%M")
    rhythm["_last_active_date"] = today_str

    _log(f"[Brain][V8] 节奏更新: hour={hour}, wake={rhythm.get('avg_wake_time', 'N/A')}, "
         f"sleep={rhythm.get('avg_sleep_time', 'N/A')}")


def _update_avg_time(rhythm, key, new_time_str, window=7):
    """滑动平均更新时间（加权：新数据权重更高）

    将时间转为分钟数进行加权平均，处理跨午夜场景。
    """
    samples_key = f"_{key}_samples"
    samples = rhythm.setdefault(samples_key, [])
    samples.append(new_time_str)
    # 只保留最近 window 个样本
    if len(samples) > window:
        samples[:] = samples[-window:]

    # 将所有样本转为分钟数并加权平均
    minutes_list = []
    for t in samples:
        try:
            parts = t.split(":")
            m = int(parts[0]) * 60 + int(parts[1])
            # wake_time 样本过滤：丢弃 12:00 之后的异常值（历史脏数据清洗）
            if "wake" in key and m >= 720:
                continue
            minutes_list.append(m)
        except (ValueError, IndexError):
            continue

    if not minutes_list:
        return

    # 处理跨午夜：如果是 sleep_time 且有 < 6:00 的数据，视为次日凌晨
    if "sleep" in key:
        adjusted = []
        for m in minutes_list:
            if m < 360:  # 6:00 之前视为次日凌晨
                adjusted.append(m + 1440)
            else:
                adjusted.append(m)
        minutes_list = adjusted

    # 加权平均（越近的权重越高：1, 1.5, 2, 2.5, ...）
    total_weight = 0
    weighted_sum = 0
    for i, m in enumerate(minutes_list):
        w = 1 + i * 0.5
        weighted_sum += m * w
        total_weight += w

    avg_minutes = int(weighted_sum / total_weight)

    # 处理跨午夜回转
    if avg_minutes >= 1440:
        avg_minutes -= 1440

    avg_h = avg_minutes // 60
    avg_m = avg_minutes % 60
    rhythm[key] = f"{avg_h:02d}:{avg_m:02d}"


def _update_weekend_shift(rhythm, wake_time_str):
    """更新周末晚起偏移量（与工作日 avg_wake_time 的差值）"""
    avg_wake = rhythm.get("avg_wake_time")
    if not avg_wake:
        return

    try:
        wake_parts = wake_time_str.split(":")
        wake_min = int(wake_parts[0]) * 60 + int(wake_parts[1])
        avg_parts = avg_wake.split(":")
        avg_min = int(avg_parts[0]) * 60 + int(avg_parts[1])
        shift = wake_min - avg_min
        if shift > 0:
            # 简单滑动平均
            old_shift = rhythm.get("weekend_shift", 60)
            rhythm["weekend_shift"] = int(old_shift * 0.7 + shift * 0.3)
    except (ValueError, IndexError):
        pass
