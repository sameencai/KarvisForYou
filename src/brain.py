# -*- coding: utf-8 -*-
"""
Karvis 大脑
核心中枢：Prompt 组装 → 多模型路由 → JSON 解析 → Skill 分发 → 记忆更新
"""
import json
import os
import sys
import time as _time
import threading
import requests
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import cnlunar
    _HAS_CNLUNAR = True
except ImportError:
    _HAS_CNLUNAR = False

from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    CLAUDE_API_KEY, CLAUDE_BASE_URL, CLAUDE_MODEL,
    QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL, QWEN_VL_MODEL,
    CHECKIN_TIMEOUT_SECONDS, SCHEDULER_RHYTHM_WINDOW,
    ADMIN_USER_ID, ALERT_SLOW_THRESHOLD, ALERT_SLOW_CONSECUTIVE,
    ALERT_COOLDOWN_SECONDS,
)
from memory import (
    load_memory,
    format_recent_messages, add_message_to_state, apply_memory_updates,
    read_state_cached, write_state_and_update_cache
)
import prompts

# 复用线程池，减少线程创建开销
_executor = ThreadPoolExecutor(max_workers=6)

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    ts = datetime.now(_BEIJING_TZ).strftime("%H:%M:%S")
    try:
        from app import _get_request_id
        rid = _get_request_id()
        if rid:
            print(f"{ts} [{rid}] {msg}", file=sys.stderr, flush=True)
            return
    except ImportError:
        pass
    print(f"{ts} {msg}", file=sys.stderr, flush=True)


# ============ 管理员告警推送 ============

_alert_state = {
    "slow_count": 0,           # 连续慢请求计数
    "last_alert_time": {},     # {alert_type: timestamp} 同类告警冷却
}


def _send_admin_alert(alert_type, message):
    """
    向管理员推送企微告警消息。
    支持冷却机制：同类告警 ALERT_COOLDOWN_SECONDS 内不重复发送。
    """
    if not ADMIN_USER_ID:
        _log(f"[Alert] 未配置 ADMIN_USER_ID，跳过告警: {alert_type}")
        return

    now = _time.time()
    last = _alert_state["last_alert_time"].get(alert_type, 0)
    if now - last < ALERT_COOLDOWN_SECONDS:
        _log(f"[Alert] 冷却中，跳过: {alert_type} (距上次{now - last:.0f}s)")
        return

    try:
        from channel_router import send_alert
        results = send_alert(f"🚨 Karvis 告警\n\n{message}")
        if any(ok for _, ok in results):
            _alert_state["last_alert_time"][alert_type] = now
            _log(f"[Alert] 已推送: {alert_type}")
        else:
            _log(f"[Alert] 推送失败: {alert_type}")
    except Exception as e:
        _log(f"[Alert] 推送异常: {e}")


def _check_and_alert(elapsed, user_id, skill, user_text, error=None):
    """
    请求完成后检查是否需要告警。
    支持：慢请求（连续N次 > 阈值）、Traceback/异常、月度预算超限。
    """
    # 1. 慢请求检测
    if elapsed > ALERT_SLOW_THRESHOLD:
        _alert_state["slow_count"] += 1
        if _alert_state["slow_count"] >= ALERT_SLOW_CONSECUTIVE:
            _send_admin_alert("slow_request",
                f"⏱ 连续 {_alert_state['slow_count']} 次慢请求 (>{ALERT_SLOW_THRESHOLD}s)\n"
                f"最新: {elapsed:.1f}s\n"
                f"用户: {user_id}\n"
                f"技能: {skill}\n"
                f"输入: {(user_text or '')[:50]}")
    else:
        _alert_state["slow_count"] = 0  # 重置连续计数

    # 2. 异常告警
    if error:
        _send_admin_alert("error",
            f"❌ 处理异常\n"
            f"用户: {user_id}\n"
            f"错误: {str(error)[:200]}\n"
            f"输入: {(user_text or '')[:50]}")

    # 3. 月度预算检查（每 50 次调用检查一次，避免频繁 IO）
    _alert_state["_call_count"] = _alert_state.get("_call_count", 0) + 1
    if _alert_state["_call_count"] % 50 == 0:
        _check_monthly_budget()


# 月度预算上限（元），可通过环境变量覆写
_MONTHLY_BUDGET = float(os.environ.get("MONTHLY_BUDGET", "50"))


def _check_monthly_budget():
    """检查当月 API 成本是否超过预算的 80%，超过则推企微告警"""
    try:
        from user_context import USAGE_LOG_FILE
        import os as _os

        if not _os.path.exists(USAGE_LOG_FILE):
            return

        now = datetime.now(timezone(timedelta(hours=8)))
        month_str = now.strftime("%Y-%m")
        month_cost = 0.0

        with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("ts", "")[:7] != month_str:
                        continue
                    m = e.get("model", "").lower()
                    pt = e.get("prompt_tokens", 0)
                    ct = e.get("completion_tokens", 0)
                    if "claude" in m:
                        # Claude Sonnet: $3/M input, $15/M output → 约 ¥21/M, ¥105/M
                        month_cost += pt / 1e6 * 21 + ct / 1e6 * 105
                    elif "deepseek" in m:
                        month_cost += pt / 1e6 * 2 + ct / 1e6 * 8
                    elif "vl" in m:
                        month_cost += pt / 1e6 * 3 + ct / 1e6 * 9
                except (json.JSONDecodeError, KeyError):
                    continue

        pct = month_cost / _MONTHLY_BUDGET * 100 if _MONTHLY_BUDGET > 0 else 0
        if pct >= 80:
            _send_admin_alert("budget_warning",
                f"💰 月度预算预警\n\n"
                f"当月已用: ¥{month_cost:.2f} / ¥{_MONTHLY_BUDGET:.0f}\n"
                f"使用率: {pct:.0f}%\n"
                f"月份: {month_str}")
    except Exception as e:
        _log(f"[Alert] 预算检查异常: {e}")


# ============ LLM 用量日志 ============

# 线程本地变量：暂存当前请求的 user_id，供 LLM 调用层记录用量
_thread_local = threading.local()


def _set_current_user(user_id):
    """设置当前线程的 user_id（在 process() 入口调用）"""
    _thread_local.user_id = user_id


def _log_llm_usage(model_tier, model_name, usage_dict, latency_s):
    """记录一次 LLM 调用的用量到 usage_log.jsonl，支持自动轮转"""
    try:
        from user_context import USAGE_LOG_FILE, SYSTEM_DIR

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

        # 自动轮转：文件超过 10MB 时归档
        _rotate_jsonl(USAGE_LOG_FILE, max_size_mb=10)

        with open(USAGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log(f"[UsageLog] 记录失败: {e}")


_JSONL_ROTATE_MAX_MB = 10  # JSONL 文件轮转阈值


def _rotate_jsonl(filepath, max_size_mb=None):
    """JSONL 文件轮转：超过阈值时重命名为 .bak 并压缩"""
    if max_size_mb is None:
        max_size_mb = _JSONL_ROTATE_MAX_MB
    try:
        if not os.path.exists(filepath):
            return
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if size_mb < max_size_mb:
            return

        now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        bak_path = f"{filepath}.{now_str}.bak"
        os.rename(filepath, bak_path)
        _log(f"[Rotate] {filepath} ({size_mb:.1f}MB) → {bak_path}")

        # 异步压缩 bak 文件（不阻塞主线程）
        def _compress():
            import gzip
            import shutil
            try:
                with open(bak_path, 'rb') as f_in:
                    with gzip.open(f"{bak_path}.gz", 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(bak_path)
                _log(f"[Rotate] 压缩完成: {bak_path}.gz")
            except Exception as e:
                _log(f"[Rotate] 压缩失败: {e}")

        _executor.submit(_compress)
    except Exception as e:
        _log(f"[Rotate] 轮转失败: {e}")


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
             temperature=0.3, enable_thinking=None, ctx=None):
    """
    统一 LLM 调用入口，支持三层模型路由 + 管理员专属 Claude + 自动降级。
    
    Args:
        model_tier: "flash" | "main" | "think"
        enable_thinking: 覆盖 thinking 设置。None = 按 tier 自动决定
        ctx: UserContext, 用于判断是否走管理员 Claude 路由
    Returns:
        str: LLM 回复文本，失败返回 None
    """
    # 管理员模型路由：管理员 + 配置了 Claude Key + 非 flash tier → 走 Claude
    use_claude = (ctx and ctx.is_admin and CLAUDE_API_KEY
                  and model_tier in ("main", "think"))

    try:
        if model_tier == "flash":
            return _call_qwen_flash(messages, max_tokens, temperature)

        if use_claude:
            return _call_claude(messages, max_tokens, temperature)

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
        if use_claude:
            _log(f"[Brain] Claude 失败: {e}, 降级到 DeepSeek")
            try:
                thinking = enable_thinking if enable_thinking is not None else (model_tier == "think")
                return _call_deepseek(messages, max_tokens, temperature,
                                      enable_thinking=thinking)
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


def _call_claude(messages, max_tokens=500, temperature=0.3):
    """调用 Claude（管理员专属），通过 OpenAI 兼容 API 代理"""
    url = f"{CLAUDE_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {CLAUDE_API_KEY}",
        "Content-Type": "application/json"
    }

    # OpenAI 兼容格式：直接传 messages（system 不用单独处理）
    data = {
        "model": CLAUDE_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    total_chars = sum(len(m.get("content", "")) for m in messages)
    _log(f"[Brain][Claude] 请求: model={CLAUDE_MODEL}, "
         f"prompt_chars={total_chars}, max_tokens={max_tokens}")

    t0 = _time.time()
    resp = requests.post(url, headers=headers, json=data, timeout=120)
    t1 = _time.time()

    if resp.status_code == 200:
        result = resp.json()
        usage = result.get("usage", {})
        # OpenAI 兼容格式：choices[0].message.content
        reply_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        _log(f"[Brain][Claude] 响应: {t1-t0:.1f}s, "
             f"prompt_tokens={usage.get('prompt_tokens')}, "
             f"completion_tokens={usage.get('completion_tokens')}")
        _log_llm_usage("claude", CLAUDE_MODEL, {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }, t1 - t0)
        return reply_text

    _log(f"[Brain][Claude] API 错误: {resp.status_code} - {resp.text[:200]}")
    raise RuntimeError(f"Claude API {resp.status_code}")


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

_WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

def _build_time_string(now_bj):
    """构建包含公历+农历的时间字符串，注入到 System Prompt。
    
    示例输出:
      2026-02-19 20:30 星期四 | 农历正月初三 | 春节假期 | 雨水
    """
    base = now_bj.strftime("%Y-%m-%d %H:%M")
    weekday = _WEEKDAY_CN[now_bj.weekday()]
    parts = [f"{base} {weekday}"]

    if _HAS_CNLUNAR:
        try:
            lunar = cnlunar.Lunar(now_bj.replace(tzinfo=None), godType='8char')
            # 农历日期
            parts.append(f"农历{lunar.lunarMonthCn}{lunar.lunarDayCn}")
            # 农历节日（如 春节、元宵、端午 等）
            festivals = []
            if lunar.lunarFestival:
                festivals.append(lunar.lunarFestival)
            if lunar.solarFestival:
                festivals.append(lunar.solarFestival)
            if festivals:
                parts.append("、".join(festivals))
            # 节气（当天恰逢节气才有值）
            st = lunar.todaySolarTerms
            if st and st != "无":
                parts.append(f"节气：{st}")
        except Exception:
            pass  # cnlunar 异常不影响主流程

    return " | ".join(parts)

def _select_rules(state, payload=None, ctx=None):
    """根据 payload.type / state / 用户文本，选择需要注入的 RULES 分段。

    方案 C: system 类型请求只注入 RULES_SYSTEM_TASKS（不含 SKILLS 和用户交互 RULES）
    方案 A: 用户消息根据 state 和关键词动态注入分段，RULES_CORE 始终注入
    V12: 管理员额外注入 RULES_FINANCE，所有用户注入 RULES_SKILLS_MGMT
    """
    # 方案 C: 定时任务走精简 prompt
    if payload and payload.get("type") == "system":
        return [prompts.RULES_SYSTEM_TASKS]

    # 方案 A: 用户消息 — CORE 始终注入，其余按需
    segments = [prompts.RULES_CORE]

    user_text = (payload.get("text", "") if payload else "").lower() if payload else ""

    # 读书/影视：仅关键词触发（去掉 state 持久字段避免每条消息都注入）
    _BOOKS_KW = ("看了", "读了", "推荐", "这本书", "书摘", "金句", "总结一下",
                 "在读", "在看", "电影", "剧", "纪录片", "动画", "影视")
    if any(kw in user_text for kw in _BOOKS_KW):
        segments.append(prompts.RULES_BOOKS_MEDIA)

    # 习惯/Top3：仅关键词触发
    _HABITS_KW = ("实验", "习惯", "top 3", "top3", "今天要做", "今天的目标",
                  "今天最重要")
    if any(kw in user_text for kw in _HABITS_KW):
        segments.append(prompts.RULES_HABITS)

    # 高级功能：语音 + 关键词触发（去掉 pending_decisions state 触发）
    _ADV_KW = ("要不要", "纠结", "犹豫", "决定了", "决策", "复盘",
               "回顾", "分析", "梳理", "深潜", "盘点", "之前写过",
               "帮我看看", "文件里")
    is_voice = payload.get("type") == "voice" if payload else False
    if is_voice or any(kw in user_text for kw in _ADV_KW):
        segments.append(prompts.RULES_ADVANCED)

    # V12: 财务规则 — 仅管理员且包含财务关键词时注入
    if ctx and ctx.is_admin:
        _FINANCE_KW = ("花了多少", "收支", "资产", "财务", "账单", "导入", "净值",
                       "财报", "月度报告", "快照")
        if any(kw in user_text for kw in _FINANCE_KW):
            segments.append(prompts.RULES_FINANCE)

    # V12: Skill 管理规则 — 关键词触发
    _SKILLS_KW = ("功能", "技能", "skill", "开启", "关闭", "禁用", "启用",
                  "关掉", "打开")
    if any(kw in user_text for kw in _SKILLS_KW):
        segments.append(prompts.RULES_SKILLS_MGMT)

    return segments


def build_system_prompt(state, ctx, prompt_futs=None, payload=None):
    """组装完整的 System Prompt（多用户版，支持用户自定义 SOUL）
    
    prompt_futs: 可选，外部提前提交的 {"mem": Future} dict，用于与 state 读取并行
    payload: 可选，当前请求 payload，用于条件 RULES 注入
    """
    beijing_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(beijing_tz)
    current_time = _build_time_string(now_bj)

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
    ai_name = ctx.get_user_config().get("ai_name", "")
    if ai_name:
        soul += f"\n- 用户给你起了昵称「{ai_name}」，在合适的时候可以用这个名字自称"

    # 方案 C+A: 条件注入 RULES（V12: 传入 ctx 用于 admin 判断）
    is_system = payload and payload.get("type") == "system"
    rules_segments = _select_rules(state, payload, ctx=ctx)
    rules_text = "\n\n".join(rules_segments)

    # V12: system 类型不注入 SKILLS；其他场景根据用户权限动态生成
    if is_system:
        skills_block = ""
    else:
        from skill_loader import get_skills_for_prompt
        allowed_names = get_skills_for_prompt(ctx)
        skills_block = prompts.build_skills_prompt(allowed_names)

    parts = [soul,
             f"\n## 长期记忆\n{mem}",
             f"\n## 最近对话\n{recent}",
             f"\n## 当前状态\n{state_summary}",
             f"\n## 当前时间\n{current_time}"]
    if skills_block:
        parts.append(f"\n{skills_block}")
    parts.append(f"\n{rules_text}")
    parts.append(f"\n{prompts.OUTPUT_FORMAT}")

    return "\n".join(parts)


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

    # 深度自问状态
    if state.get("reflect_pending"):
        reflect_q = state.get("reflect_question", "")
        reflect_cat = state.get("reflect_category", "")
        parts.append(f"深度自问进行中: [{reflect_cat}] \"{reflect_q}\"")

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

    # 0. 预热存储连接（OneDrive 模式需要预热 token + TLS 连接）
    if ctx and hasattr(ctx.IO, 'get_token'):
        ctx.IO.get_token()
    t_token = _time.time()
    _log(f"[Brain][耗时] 存储预热: {t_token - t_start:.1f}s")

    # 1. 读取 state 和 memory（并发，按用户隔离）
    state_future = _executor.submit(read_state_cached, ctx)
    prompt_futs = {
        "mem": _executor.submit(load_memory, ctx),
    }

    # 2. 先提取 user_text（不依赖 state 和 prompt，CPU 操作）
    #    图片消息：管理员调 VL 模型获取描述；非管理员返回"敬请期待"
    if payload.get("type") == "image" and payload.get("image_base64"):
        if ctx and ctx.is_admin:
            _log("[Brain] 检测到图片（管理员），调用千问 VL 进行图像理解...")
            vl_desc = _call_qwen_vl(payload["image_base64"])
            if vl_desc:
                payload["image_description"] = vl_desc
                _log(f"[Brain] 图像理解完成: {vl_desc[:100]}")
            else:
                _log("[Brain] 图像理解失败，降级为普通图片处理")
        else:
            _log(f"[Brain] 非管理员图片消息，跳过 VL 调用, user={user_id}")
        # 释放 base64 数据，节省内存
        del payload["image_base64"]
        # V12: 非管理员图片 — 记录附件到 Quick-Notes，回复"敬请期待"，跳过 LLM 调用
        if not (ctx and ctx.is_admin):
            state = state_future.result() or {}
            _save_to_quick_notes(payload, state, ctx)
            return {"reply": "图片已保存~ 图片理解功能即将在订阅版上线，敬请期待~"}

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
    system_prompt = build_system_prompt(state, ctx, prompt_futs=prompt_futs, payload=payload)
    t_prompt = _time.time()
    _log(f"[Brain][耗时] prompt组装: {t_prompt - t_state:.1f}s (prompt长度={len(system_prompt)})")

    user_message = _build_user_message(payload)

    # 多模型路由：根据请求类型选择模型层级
    is_system = payload.get("type") == "system"
    action = payload.get("action", "") if is_system else None
    model_tier = _select_model_tier(payload, is_system_action=is_system, action=action)
    use_claude = ctx and ctx.is_admin and CLAUDE_API_KEY and model_tier in ("main", "think")
    _log(f"[Brain] 模型路由: tier={model_tier}, is_system={is_system}, action={action}"
         f"{', model=Claude' if use_claude else ', model=DeepSeek'}")

    llm_response = call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ], model_tier=model_tier, ctx=ctx)
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

    # 7. Quick-Notes 两阶段过滤（V-Web-01）
    #    Stage 1: 规则预筛 — 已由 Skill handler 结构化处理的消息直接跳过
    #    Stage 2: Flash 后判 — 回复发出后异步调 Flash 判断是否值得写入
    primary_skill = _get_primary_skill(decision)

    # Reflect 防护 — reflect_pending 时，非 reflect skill 强制重路由（优先级低于 checkin）
    _REFLECT_SKILLS = ("reflect.answer", "reflect.skip", "reflect.history", "reflect.push")
    _CHECKIN_SKILLS = ("checkin.answer", "checkin.skip", "checkin.cancel", "checkin.start")
    if (state.get("reflect_pending")
            and not state.get("checkin_pending")
            and payload.get("type") != "system"
            and primary_skill not in _REFLECT_SKILLS
            and primary_skill not in _CHECKIN_SKILLS):
        _log(f"[Brain] 深度自问防护: {primary_skill} → reflect.answer")
        decision["skill"] = "reflect.answer"
        decision["params"] = {"answer": user_text}
        decision.pop("steps", None)
        primary_skill = "reflect.answer"

    _pending_note_filter = False  # 是否需要 Flash 后判
    if payload.get("type") != "system" and primary_skill not in ("checkin.answer", "checkin.skip", "checkin.cancel", "checkin.start", "reflect.answer", "reflect.skip"):
        if primary_skill in _SKIP_NOTE_SKILLS:
            _log(f"[Brain][NoteFilter] 规则跳过: skill={primary_skill}")
        elif primary_skill == "note.save":
            # 用户明确要求记录，直接写入
            _save_to_quick_notes(payload, state, ctx)
        else:
            # 需要 Flash 后判（在回复发送后异步执行）
            _pending_note_filter = True

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

    # V-Web-01: 回复发出后异步 Flash 过滤 Quick-Notes
    if _pending_note_filter:
        _executor.submit(_flash_filter_and_save, payload, state, ctx, primary_skill)

    # V8: 更新用户节奏画像（纯数据收集，不影响回复）
    try:
        _update_user_rhythm(state)
    except Exception as e:
        _log(f"[Brain][V8] 节奏更新失败（不影响主流程）: {e}")

    t_save_start = _time.time()
    _save_state_and_memory(state, decision, payload=payload, reply=reply, elapsed=t_save_start - t_start, ctx=ctx)
    t_end = _time.time()
    _log(f"[Brain][耗时] 保存state: {t_end - t_save_start:.1f}s | 总计: {t_end - t_start:.1f}s")

    # 11. 异步告警检测（不阻塞返回）
    total_elapsed = t_end - t_start
    _executor.submit(_check_and_alert, total_elapsed, user_id,
                     _get_primary_skill(decision) if decision else "unknown",
                     user_text, None)

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
    """将每次决策写入 JSONL 日志（追加模式）
    V12 隐私保护：不记录用户输入原文、thinking 原文、回复原文。
    只保留技术性字段供运维排障。
    """
    try:
        beijing_tz = timezone(timedelta(hours=8))
        now_str = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")

        input_type = payload.get("type", "") if payload else ""
        # system action 记录 action 名称（非用户隐私）
        action = payload.get("action", "") if input_type == "system" else ""

        entry = {
            "ts": now_str,
            "user_id": ctx.user_id if ctx else "",
            "input_type": input_type,
            "action": action,
            "skill": decision.get("skill", "") if decision else "",
            "has_memory_updates": bool(decision.get("memory_updates")) if decision else False,
            "has_reply": bool(reply),
            "elapsed_s": round(elapsed, 1) if elapsed else None,
        }
        # 注入 Request ID（如果有）
        try:
            from app import _get_request_id
            rid = _get_request_id()
            if rid:
                entry["request_id"] = rid
        except ImportError:
            pass
        line = json.dumps(entry, ensure_ascii=False)

        log_file = ctx.decision_log_file if ctx else ""
        if log_file and ctx:
            # 本地存储才做轮转检查（OneDrive 不支持 rename）
            if ctx.storage_mode == "local":
                _rotate_jsonl(log_file, max_size_mb=5)
            existing = ctx.IO.read_text(log_file) or ""
            new_content = existing + line + "\n"
            ctx.IO.write_text(log_file, new_content)
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
        llm_response = call_llm(messages, model_tier="main", max_tokens=500, temperature=0.3, ctx=ctx)
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
    "todo.list", "todo.edit", "todo.delete", "todo.remind_cancel",
    "checkin.start", "checkin.answer", "checkin.skip", "checkin.cancel",
    "book.create", "book.excerpt", "book.thought", "book.summary", "book.quotes",
    "media.create", "media.thought",
    "mood.generate", "voice.journal",
    "settings.nickname", "settings.ai_name", "settings.soul", "settings.info",
    "web.token",
    "habit.propose", "habit.nudge", "habit.status", "habit.complete",
    "decision.record", "dynamic",
    "reflect.push", "reflect.answer", "reflect.skip", "reflect.history",
    "deep.dive",
})

# ── 速记智能过滤：规则预筛跳过集合（V-Web-01）──
# 这些 skill 的消息已由对应 handler 结构化处理，无需重复写入 Quick-Notes
_SKIP_NOTE_SKILLS = frozenset({
    "todo.add", "todo.done", "todo.list",
    "habit.propose", "habit.nudge", "habit.status", "habit.complete",
    "decision.record", "decision.review", "decision.list",
    "book.create", "book.excerpt", "book.thought", "book.summary", "book.quotes",
    "media.create", "media.thought",
    "web.token",
    "settings.nickname", "settings.ai_name", "settings.soul", "settings.info",
    "deep.dive",
})


def _get_primary_skill(decision):
    """从 decision 中提取主 skill 名称（兼容 steps 和旧格式）"""
    steps = decision.get("steps")
    if steps and len(steps) > 0:
        return steps[0].get("skill", "ignore")
    return decision.get("skill", "ignore")


def _execute_steps(decision, state, registry, ctx):
    """
    V4+V12: 执行 steps 数组中的所有 skill，收集结果。
    兼容旧格式（单 skill + params）。
    V12: 执行前检查 Skill 权限（visibility + 用户黑白名单）。
    """
    steps = decision.get("steps")
    if not steps:
        skill = decision.get("skill", "ignore")
        params = decision.get("params", {})
        steps = [{"skill": skill, "params": params}]

    # V12: 加载 Skill 元数据用于 visibility 检查
    from skill_loader import get_skill_metadata
    all_metadata = get_skill_metadata()

    results = []
    for i, step in enumerate(steps):
        skill_name = step.get("skill", "ignore")
        params = step.get("params", {})

        if skill_name == "note.save":
            _log(f"[Brain] Step {i}: note.save 已由统一写入处理，跳过")
            results.append({"skill": skill_name, "result": {"success": True}})
            continue
        if skill_name == "ignore":
            results.append({"skill": skill_name, "result": {"success": True}})
            continue

        # V12: 执行层权限检查（三级拦截）
        meta = all_metadata.get(skill_name, {})
        vis = meta.get("visibility", "public")

        # 1) private Skill → 非管理员完全不透露存在
        if vis == "private" and not ctx.is_admin:
            _log(f"[Brain] Step {i}: {skill_name} 是 private，用户 {ctx.user_id} 无权限")
            results.append({"skill": skill_name, "result": {
                "success": False,
                "reply_override": "我目前没有这个功能哦~ 如果你想管理待办或记笔记，随时告诉我~"
            }})
            continue

        # 2) preview Skill → 显示"敬请期待"
        if vis == "preview" and not ctx.is_admin:
            preview_msg = meta.get("preview_message",
                                   "该功能即将在订阅版上线，敬请期待~ 目前你可以用文字描述给我，我一样能帮到你~")
            _log(f"[Brain] Step {i}: {skill_name} 是 preview，返回预告")
            results.append({"skill": skill_name, "result": {
                "success": False,
                "reply_override": preview_msg
            }})
            continue

        # 3) 用户级黑白名单
        if not ctx.is_skill_allowed(skill_name):
            _log(f"[Brain] Step {i}: {skill_name} 被用户 {ctx.user_id} 禁用")
            results.append({"skill": skill_name, "result": {
                "success": False,
                "reply_override": f"「{skill_name.split('.')[0]}」功能未开启，你可以说「开启{skill_name.split('.')[0]}」来启用~"
            }})
            continue

        handler = registry.get(skill_name)
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
    V4+V12: 智能回复路由。
    简单 skill → 直接用 decision.reply 或 skill.reply
    复杂场景 → Flash 二次加工
    V12: reply_override 优先（执行层权限拦截时使用）
    """
    all_skills = [s.get("skill", "ignore") for s in steps]
    llm_reply = decision.get("reply")

    # V12: 检查是否有 reply_override（执行层权限拦截）
    for sr in step_results:
        r = sr.get("result", {})
        if isinstance(r, dict) and r.get("reply_override"):
            return r["reply_override"]

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

def _flash_filter_and_save(payload, state, ctx, primary_skill):
    """回复后异步执行：用 Flash 判断消息是否值得写入 Quick-Notes（V-Web-01）"""
    text = _extract_user_text(payload)
    if not text or not text.strip():
        return
    try:
        result = call_llm([
            {"role": "system", "content": prompts.FLASH_NOTE_FILTER},
            {"role": "user", "content": text}
        ], model_tier="flash", max_tokens=5, temperature=0)
        should_save = result and result.strip().upper().startswith("YES")
        if should_save:
            _save_to_quick_notes(payload, state, ctx)
            _log(f"[Brain][NoteFilter] Flash判断写入: skill={primary_skill}, text={text[:40]}...")
        else:
            _log(f"[Brain][NoteFilter] Flash判断跳过: skill={primary_skill}, text={text[:40]}...")
    except Exception as e:
        _log(f"[Brain][NoteFilter] Flash判断失败，兜底写入: {e}")
        _save_to_quick_notes(payload, state, ctx)


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
