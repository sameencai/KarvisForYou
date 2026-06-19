# -*- coding: utf-8 -*-
"""
Skill: memory.maintain
周度记忆维护 — 每周日周报之后自动触发，精炼长期记忆。

功能：
1. 更新"人物画像摘要"（3-5 句总览）
2. 更新"活跃主题"（本周 top 3 活跃议题）
3. 更新"核心模式"（长期不变的行为/认知模式）
4. 淘汰"近期关注"中超过 30 天且已不活跃的条目
"""
import json
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    from logger import log
    log(msg)


MEMORY_MAINTAIN_PROMPT = """你是一个记忆整理助手。根据以下用户的完整记忆文件和最近一周的对话摘要，执行记忆维护任务。

## 任务

1. **人物画像摘要**（3-5 句话）：这个人是谁、当前核心矛盾、积极信号、当前阶段。简洁、不评判、不鸡汤。

2. **活跃主题**（最多 5 个）：本周用户最关注/投入精力最多的议题。每条一句话，带简短说明。

3. **核心模式**（最多 5 个）：跨越时间反复出现的行为/认知/情绪模式。每条用"触发→反应→结果"的结构描述。

4. **淘汰建议**：列出"近期关注"中超过 30 天且在最近对话中已不再活跃的条目（列出条目原文即可）。

## 输出格式（严格 JSON）
```json
{
  "profile_summary": "3-5 句人物画像",
  "active_topics": ["主题1: 说明", "主题2: 说明", ...],
  "core_patterns": ["模式1: 触发→反应→结果", ...],
  "stale_items": ["要淘汰的近期关注条目原文", ...]
}
```

只输出 JSON，不加其他内容。"""


def execute(params, state, ctx):
    """
    执行周度记忆维护。
    由 weekly_review 完成后自动触发，或手动调用。
    """
    from brain import call_llm
    from memory import load_memory, format_recent_messages

    _log(f"[memory.maintain] 开始记忆维护, user={ctx.user_id}")

    # 读取完整 memory
    full_memory = load_memory(ctx)
    if not full_memory:
        _log(f"[memory.maintain] memory 为空, 跳过")
        return {"success": True, "skipped": True}

    # 读取最近对话作为本周信号
    recent = format_recent_messages(state)

    # 构建 user message
    user_msg = f"## 完整记忆文件\n{full_memory}\n\n## 最近对话\n{recent}"

    # 调用 LLM
    response = call_llm([
        {"role": "system", "content": MEMORY_MAINTAIN_PROMPT},
        {"role": "user", "content": user_msg}
    ], model_tier="main", max_tokens=800, temperature=0.3)

    if not response:
        _log(f"[memory.maintain] LLM 返回空")
        return {"success": False, "error": "LLM 无响应"}

    # 解析 JSON
    try:
        # 去掉 markdown 代码块
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        result = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 JSON
        start = response.find("{")
        end = response.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(response[start:end + 1])
            except json.JSONDecodeError:
                _log(f"[memory.maintain] JSON 解析失败: {response[:200]}")
                return {"success": False, "error": "JSON 解析失败"}
        else:
            _log(f"[memory.maintain] 无法提取 JSON: {response[:200]}")
            return {"success": False, "error": "无 JSON 输出"}

    # 应用维护结果到 memory.md
    profile = result.get("profile_summary", "")
    topics = result.get("active_topics", [])
    patterns = result.get("core_patterns", [])
    stale = result.get("stale_items", [])

    _apply_maintenance(full_memory, profile, topics, patterns, stale, ctx)

    _log(f"[memory.maintain] 维护完成: profile={len(profile)}字, "
         f"topics={len(topics)}, patterns={len(patterns)}, stale={len(stale)}")

    return {
        "success": True,
        "reply": None,
        "maintained": {
            "profile_updated": bool(profile),
            "topics_count": len(topics),
            "patterns_count": len(patterns),
            "stale_removed": len(stale),
        }
    }


def _apply_maintenance(full_memory, profile, topics, patterns, stale, ctx):
    """将维护结果写回 memory.md"""
    import re

    memory = full_memory

    # 1. 更新人物画像摘要
    if profile:
        new_section = f"## 人物画像摘要\n{profile}"
        if "## 人物画像摘要" in memory:
            # 替换现有章节
            memory = re.sub(
                r'## 人物画像摘要\n.*?(?=\n## )',
                new_section + "\n\n",
                memory,
                flags=re.DOTALL
            )
        else:
            # 在 # Memory 后插入
            memory = memory.replace("# Memory\n", f"# Memory\n\n{new_section}\n\n", 1)

    # 2. 更新活跃主题
    if topics:
        topics_text = "\n".join(f"- {t}" for t in topics)
        new_section = f"## 活跃主题\n{topics_text}"
        if "## 活跃主题" in memory:
            memory = re.sub(
                r'## 活跃主题\n.*?(?=\n## )',
                new_section + "\n\n",
                memory,
                flags=re.DOTALL
            )
        else:
            # 在画像摘要后插入
            if "## 人物画像摘要" in memory:
                memory = re.sub(
                    r'(## 人物画像摘要\n.*?)\n(## )',
                    r'\1\n\n' + new_section + r'\n\n\2',
                    memory,
                    flags=re.DOTALL,
                    count=1
                )
            else:
                memory = memory.replace("# Memory\n", f"# Memory\n\n{new_section}\n\n", 1)

    # 3. 更新核心模式
    if patterns:
        patterns_text = "\n".join(f"- {p}" for p in patterns)
        new_section = f"## 核心模式\n{patterns_text}"
        if "## 核心模式" in memory:
            memory = re.sub(
                r'## 核心模式\n.*?(?=\n## )',
                new_section + "\n\n",
                memory,
                flags=re.DOTALL
            )
        else:
            # 在活跃主题后插入
            if "## 活跃主题" in memory:
                memory = re.sub(
                    r'(## 活跃主题\n.*?)\n(## )',
                    r'\1\n\n' + new_section + r'\n\n\2',
                    memory,
                    flags=re.DOTALL,
                    count=1
                )

    # 4. 淘汰近期关注中的过期条目
    if stale and "## 近期关注" in memory:
        for item in stale:
            # 精确匹配移除该行
            item_escaped = item.strip().lstrip("- ")
            lines = memory.split("\n")
            new_lines = [l for l in lines if item_escaped not in l]
            if len(new_lines) < len(lines):
                memory = "\n".join(new_lines)
                _log(f"[memory.maintain] 淘汰条目: {item_escaped[:40]}")

    # 写回文件
    ok = ctx.IO.write_text(ctx.memory_file, memory)
    if ok:
        # 清除缓存
        from memory import _prompt_cache
        _prompt_cache.invalidate(ctx.memory_file)
        _log(f"[memory.maintain] memory.md 已更新并清除缓存")
    else:
        _log(f"[memory.maintain] memory.md 写入失败")


# Skill 注册
SKILL_REGISTRY = {
    "memory.maintain": execute,
}
