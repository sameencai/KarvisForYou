# -*- coding: utf-8 -*-
"""
Gateway Loop — 统一的 LLM Tool-Call 循环。

借鉴 OpenClaw Gateway 设计：
1. 组装 Prompt + Tool Definitions
2. 发给 LLM
3. LLM 返回 tool_call(s) → 执行工具 → 结果追加到对话 → 再次调用 LLM
4. LLM 返回纯文本 → 结束循环，发给用户

替代原 brain.py 中的：
- _parse_llm_output()  — 不再需要 JSON 容错解析
- _run_agent_loop()    — 统一为 gateway_loop
- _execute_steps()     — 合并到 loop 中
- _resolve_reply()     — LLM 最终文本回复即为结果
"""
import json
import time as _time
from concurrent.futures import ThreadPoolExecutor

def _log(msg):
    from logger import log
    log(msg)


# ============ Gateway 主循环 ============

MAX_TOOL_ROUNDS = 8  # 最大工具调用轮数（比旧 Agent Loop 的 5 轮更宽松）


def gateway_loop(messages, tools, registry, ctx, state,
                 model_tier="main", max_tokens=800, temperature=0.3,
                 max_rounds=None):
    """
    统一的 Tool-Call Gateway Loop。

    Args:
        messages: OpenAI 格式消息列表（含 system + user）
        tools: OpenAI tools 数组（可为空 → 纯对话模式）
        registry: {skill_name: handler_fn} Skill 注册表
        ctx: UserContext
        state: 用户 state dict（可被 handler 修改）
        model_tier: 模型层级
        max_tokens: LLM 最大输出 token
        temperature: 温度
        max_rounds: 最大循环轮数（默认 MAX_TOOL_ROUNDS）

    Returns:
        GatewayResult: 包含最终回复、执行的 skill 列表、state_updates、memory_updates 等
    """
    from brain import call_llm
    from skill_loader import get_skill_metadata

    if max_rounds is None:
        max_rounds = MAX_TOOL_ROUNDS

    all_metadata = get_skill_metadata()
    executed_skills = []       # [(skill_name, params, result)]
    all_state_updates = {}
    all_memory_updates = []

    for round_idx in range(max_rounds):
        _log(f"[Gateway] Round {round_idx + 1}, messages={len(messages)}, tools={len(tools) if tools else 0}")

        t0 = _time.time()
        response = call_llm_with_tools(
            messages, tools=tools, model_tier=model_tier,
            max_tokens=max_tokens, temperature=temperature, ctx=ctx
        )
        t1 = _time.time()
        _log(f"[Gateway] LLM responded in {t1-t0:.1f}s")

        if response is None:
            _log("[Gateway] LLM returned None, breaking")
            break

        # --- Case A: 纯文本回复 → 结束循环 ---
        if response["type"] == "text":
            final_reply = response["content"]
            _log(f"[Gateway] Final text reply ({len(final_reply)} chars)")
            return GatewayResult(
                reply=final_reply,
                executed_skills=executed_skills,
                state_updates=all_state_updates,
                memory_updates=all_memory_updates,
            )

        # --- Case B: tool_call(s) → 执行并追加结果 ---
        if response["type"] == "tool_calls":
            tool_calls = response["tool_calls"]
            # 追加 assistant message（带 tool_calls）
            messages.append(response["assistant_message"])

            for tc in tool_calls:
                skill_name = tc["function"]["name"]
                try:
                    params = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, TypeError):
                    params = {}

                _log(f"[Gateway] Executing tool: {skill_name}, params_keys={list(params.keys())}")

                # 权限检查
                result = _check_and_execute(
                    skill_name, params, registry, all_metadata, state, ctx
                )
                executed_skills.append((skill_name, params, result))

                # 收集 state_updates 和 memory_updates
                if isinstance(result, dict):
                    if result.get("state_updates"):
                        all_state_updates.update(result["state_updates"])
                        state.update(result["state_updates"])
                    if result.get("memory_updates"):
                        all_memory_updates.extend(result["memory_updates"])

                # 构建 tool result message
                tool_result_content = _format_tool_result(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result_content,
                })

            # 继续循环，让 LLM 看到工具结果后决定下一步
            continue

    # 达到最大轮数仍未结束 — 尝试从最后一轮提取回复
    _log(f"[Gateway] Max rounds ({max_rounds}) reached")
    # 最后一次不带 tools 调用，强制文本回复
    fallback = call_llm_with_tools(
        messages, tools=None, model_tier=model_tier,
        max_tokens=max_tokens, temperature=temperature, ctx=ctx
    )
    final_reply = fallback["content"] if fallback and fallback["type"] == "text" else ""

    return GatewayResult(
        reply=final_reply,
        executed_skills=executed_skills,
        state_updates=all_state_updates,
        memory_updates=all_memory_updates,
    )


# ============ 结果容器 ============

class GatewayResult:
    """Gateway Loop 的返回结果"""

    def __init__(self, reply="", executed_skills=None, state_updates=None, memory_updates=None):
        self.reply = reply
        self.executed_skills = executed_skills or []
        self.state_updates = state_updates or {}
        self.memory_updates = memory_updates or []

    @property
    def primary_skill(self):
        """第一个执行的 skill 名称"""
        if self.executed_skills:
            return self.executed_skills[0][0]
        return "ignore"

    @property
    def has_tool_calls(self):
        return len(self.executed_skills) > 0

    def to_decision_dict(self):
        """向后兼容：转为旧 decision 格式"""
        return {
            "skill": self.primary_skill,
            "params": self.executed_skills[0][1] if self.executed_skills else {},
            "reply": self.reply,
            "state_updates": self.state_updates,
            "memory_updates": self.memory_updates,
        }


# ============ LLM 调用（支持 tools） ============

def call_llm_with_tools(messages, tools=None, model_tier="main",
                        max_tokens=800, temperature=0.3, ctx=None):
    """
    调用 LLM，支持 tools（Function Calling）。

    返回:
        {"type": "text", "content": str}  — 纯文本回复
        {"type": "tool_calls", "tool_calls": [...], "assistant_message": dict}  — 工具调用
        None — 失败
    """
    import requests
    from config import (
        DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
        CLAUDE_API_KEY, CLAUDE_BASE_URL, CLAUDE_MODEL,
        QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL,
    )
    from brain import _log_llm_usage, _BEIJING_TZ

    use_claude = (ctx and ctx.is_admin and CLAUDE_API_KEY
                  and model_tier in ("main", "think"))

    # --- Flash tier: 不支持 tools（用于简单判断），走旧路径 ---
    if model_tier == "flash":
        from brain import call_llm
        text = call_llm(messages, model_tier="flash", max_tokens=max_tokens,
                        temperature=temperature)
        if text:
            return {"type": "text", "content": text}
        return None

    # --- 构建请求 ---
    if use_claude:
        url = f"{CLAUDE_BASE_URL}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {CLAUDE_API_KEY}",
            "Content-Type": "application/json",
        }
        model = CLAUDE_MODEL
        tier_label = "Claude"
        timeout = 120
    else:
        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        model = DEEPSEEK_MODEL
        tier_label = "Main"
        timeout = 60

    data = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # 添加 tools（如果有）
    if tools:
        data["tools"] = tools
        data["tool_choice"] = "auto"

    # DeepSeek V3.2 thinking 控制
    if not use_claude and "v3.2" in DEEPSEEK_MODEL:
        data["enable_thinking"] = (model_tier == "think")

    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    _log(f"[Gateway][{tier_label}] 请求: model={model}, "
         f"tools={len(tools) if tools else 0}, prompt_chars={total_chars}")

    t0 = _time.time()
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=timeout)
    except Exception as e:
        _log(f"[Gateway][{tier_label}] 请求异常: {e}")
        # 降级：Claude 失败尝试 DeepSeek
        if use_claude:
            _log(f"[Gateway] Claude 失败，降级到 DeepSeek")
            return _fallback_deepseek(messages, tools, max_tokens, temperature, model_tier)
        return None
    t1 = _time.time()

    if resp.status_code != 200:
        _log(f"[Gateway][{tier_label}] API 错误: {resp.status_code} - {resp.text[:200]}")
        if use_claude:
            return _fallback_deepseek(messages, tools, max_tokens, temperature, model_tier)
        return None

    result = resp.json()
    usage = result.get("usage", {})
    _log(f"[Gateway][{tier_label}] 响应: {t1-t0:.1f}s, "
         f"prompt_tokens={usage.get('prompt_tokens')}, "
         f"completion_tokens={usage.get('completion_tokens')}")
    _log_llm_usage(model_tier, model, usage, t1 - t0)

    # --- 解析响应 ---
    choice = result.get("choices", [{}])[0]
    message = choice.get("message", {})

    # 检查是否有 tool_calls
    tool_calls = message.get("tool_calls")
    if tool_calls:
        return {
            "type": "tool_calls",
            "tool_calls": tool_calls,
            "assistant_message": message,
        }

    # 纯文本回复
    content = message.get("content", "")
    # 剥离 <think> 标签（DeepSeek thinking mode 可能包含）
    if "<think>" in content:
        think_end = content.find("</think>")
        if think_end >= 0:
            content = content[think_end + len("</think>"):].strip()
    return {"type": "text", "content": content}


def _fallback_deepseek(messages, tools, max_tokens, temperature, model_tier):
    """Claude 失败时降级到 DeepSeek"""
    import requests
    from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    from brain import _log_llm_usage

    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        data["tools"] = tools
        data["tool_choice"] = "auto"
    if "v3.2" in DEEPSEEK_MODEL:
        data["enable_thinking"] = (model_tier == "think")

    t0 = _time.time()
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=60)
        t1 = _time.time()
        if resp.status_code != 200:
            _log(f"[Gateway][Fallback] DeepSeek API 错误: {resp.status_code}")
            return None
        result = resp.json()
        usage = result.get("usage", {})
        _log_llm_usage(model_tier, DEEPSEEK_MODEL, usage, t1 - t0)
        choice = result.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls")
        if tool_calls:
            return {"type": "tool_calls", "tool_calls": tool_calls, "assistant_message": message}
        content = message.get("content", "")
        if "<think>" in content:
            think_end = content.find("</think>")
            if think_end >= 0:
                content = content[think_end + len("</think>"):].strip()
        return {"type": "text", "content": content}
    except Exception as e:
        _log(f"[Gateway][Fallback] DeepSeek 也失败: {e}")
        return None


# ============ 工具执行 ============

def _check_and_execute(skill_name, params, registry, all_metadata, state, ctx):
    """执行前权限检查 + 执行 Skill handler"""
    # 特殊处理
    if skill_name == "note.save":
        _log(f"[Gateway] note.save 已由统一写入处理，跳过执行")
        return {"success": True}
    if skill_name == "ignore":
        return {"success": True}

    # 权限检查
    meta = all_metadata.get(skill_name, {})
    vis = meta.get("visibility", "public")

    if vis == "private" and not ctx.is_admin:
        _log(f"[Gateway] {skill_name} 是 private，用户 {ctx.user_id} 无权限")
        return {"success": False, "error": "无权限", "reply": "我目前没有这个功能哦~"}

    if vis == "preview" and not ctx.is_admin:
        preview_msg = meta.get("preview_message",
                               "该功能即将在订阅版上线，敬请期待~")
        return {"success": False, "error": "preview", "reply": preview_msg}

    if not ctx.is_skill_allowed(skill_name):
        return {"success": False, "error": "disabled",
                "reply": f"「{skill_name.split('.')[0]}」功能未开启，你可以说「开启{skill_name.split('.')[0]}」来启用~"}

    # 执行
    handler = registry.get(skill_name)
    if not handler:
        _log(f"[Gateway] 未知 skill: {skill_name}")
        return {"success": False, "error": f"未知 skill: {skill_name}"}

    try:
        result = handler(params, state, ctx)
        return result or {"success": True}
    except Exception as e:
        _log(f"[Gateway] Skill {skill_name} 执行异常: {e}")
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "error": str(e)}


def _format_tool_result(result):
    """将 Skill 执行结果格式化为 tool message content"""
    if not isinstance(result, dict):
        return json.dumps({"success": True}, ensure_ascii=False)

    # agent_context（internal.* 用） — 直接返回给 LLM
    if result.get("agent_context"):
        return json.dumps(result["agent_context"], ensure_ascii=False)[:3000]

    # 普通结果
    output = {}
    if "success" in result:
        output["success"] = result["success"]
    if result.get("reply"):
        output["reply"] = result["reply"]
    if result.get("error"):
        output["error"] = result["error"]
    # 精简输出，避免过多 token
    return json.dumps(output, ensure_ascii=False)[:2000]
