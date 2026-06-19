# -*- coding: utf-8 -*-
"""
Tool Schema 定义 — 将 Skill 元数据转化为 OpenAI Function Calling 的 tools 格式。

DeepSeek V3 / Claude 都兼容 OpenAI 的 tools 格式：
[{"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}]

本模块从 prompts.SKILL_PROMPT_LINES 自动生成，保持与旧系统兼容。
"""

# ============ Skill 参数 JSON Schema 定义 ============
# 每个 skill 的参数 schema（OpenAI function calling 格式）
# 只定义需要工具调用的 skill，纯闲聊由 LLM 直接回复

SKILL_SCHEMAS = {
    "note.save": {
        "description": "保存到 Quick-Notes",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "笔记内容"},
                "attachment": {"type": "string", "description": "附件路径（可选）"},
            },
            "required": ["content"],
        },
    },
    "checkin.answer": {
        "description": "回答打卡问题",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "用户的回答"},
                "step": {"type": "integer", "description": "当前步骤"},
            },
            "required": ["answer", "step"],
        },
    },
    "checkin.skip": {
        "description": "跳过打卡题",
        "parameters": {
            "type": "object",
            "properties": {
                "step": {"type": "integer", "description": "跳过的步骤"},
            },
        },
    },
    "checkin.cancel": {
        "description": "取消打卡",
        "parameters": {"type": "object", "properties": {}},
    },
    "checkin.start": {
        "description": "启动打卡",
        "parameters": {"type": "object", "properties": {}},
    },
    "todo.add": {
        "description": "添加待办。remind_at格式YYYY-MM-DD HH:MM（一次性）或HH:MM（循环）。recur=daily/weekday/weekly/monthly，仅用户明确说了循环词才填。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "待办内容"},
                "due_date": {"type": "string", "description": "截止日期 YYYY-MM-DD"},
                "remind_at": {"type": "string", "description": "提醒时间"},
                "recur": {"type": "string", "enum": ["daily", "weekday", "weekly", "monthly", ""], "description": "循环规则"},
                "recur_spec": {"type": "object", "description": "循环细节"},
            },
            "required": ["content"],
        },
    },
    "todo.done": {
        "description": "完成待办（keyword模糊匹配或indices序号）",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "模糊匹配关键词"},
                "indices": {"type": "array", "items": {"type": "integer"}, "description": "序号列表"},
            },
        },
    },
    "todo.edit": {
        "description": "修改待办",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "index": {"type": "integer"},
                "new_content": {"type": "string"},
                "new_due_date": {"type": "string"},
                "new_remind_at": {"type": "string"},
                "new_recur": {"type": "string"},
            },
        },
    },
    "todo.delete": {
        "description": "删除待办（用户说'不做了/删掉'）",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "indices": {"type": "array", "items": {"type": "integer"}},
            },
        },
    },
    "todo.remind_cancel": {
        "description": "取消循环提醒",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    },
    "todo.list": {
        "description": "查看待办列表",
        "parameters": {"type": "object", "properties": {}},
    },
    "classify.archive": {
        "description": "归档（category: work|emotion|fun|misc, title≤10字, merge=true合并到上一条）",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["work", "emotion", "fun", "misc"]},
                "title": {"type": "string", "description": "标题≤10字"},
                "content": {"type": "string", "description": "归档内容"},
                "attachment": {"type": "string"},
                "merge": {"type": "boolean", "description": "是否合并到上一条"},
            },
            "required": ["category", "title", "content"],
        },
    },
    "daily.generate": {
        "description": "生成日报",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
            },
        },
    },
    "book.create": {
        "description": "创建读书笔记",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "author": {"type": "string"},
                "category": {"type": "string"},
                "description": {"type": "string"},
                "thought": {"type": "string"},
            },
            "required": ["name", "author", "category", "description"],
        },
    },
    "book.excerpt": {
        "description": "添加书摘",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "book": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    "book.thought": {
        "description": "添加读书感想",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "book": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    "book.summary": {
        "description": "AI生成读书总结",
        "parameters": {
            "type": "object",
            "properties": {"book": {"type": "string"}},
        },
    },
    "book.quotes": {
        "description": "AI提炼金句",
        "parameters": {
            "type": "object",
            "properties": {"book": {"type": "string"}},
        },
    },
    "media.create": {
        "description": "创建影视笔记",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "director": {"type": "string"},
                "media_type": {"type": "string"},
                "year": {"type": "string"},
                "description": {"type": "string"},
                "thought": {"type": "string"},
            },
            "required": ["name", "director", "media_type", "year", "description"],
        },
    },
    "media.thought": {
        "description": "添加影视感想",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "media": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    "mood.generate": {
        "description": "生成情绪日记",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
    },
    "weekly.review": {
        "description": "生成周回顾",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
    },
    "habit.propose": {
        "description": "提议微习惯实验",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "hypothesis": {"type": "string"},
                "triggers": {"type": "array", "items": {"type": "string"}},
                "micro_action": {"type": "string"},
                "duration_days": {"type": "integer"},
                "start_date": {"type": "string"},
            },
            "required": ["name", "hypothesis", "triggers", "micro_action"],
        },
    },
    "habit.nudge": {
        "description": "实验触发/接受/拒绝",
        "parameters": {
            "type": "object",
            "properties": {
                "trigger_text": {"type": "string"},
                "accepted": {"type": "boolean"},
            },
        },
    },
    "habit.status": {
        "description": "查看实验进度",
        "parameters": {"type": "object", "properties": {}},
    },
    "habit.complete": {
        "description": "结束实验",
        "parameters": {
            "type": "object",
            "properties": {
                "result_summary": {"type": "string"},
                "success": {"type": "boolean"},
            },
        },
    },
    "decision.record": {
        "description": "记录重要决策",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "decision": {"type": "string"},
                "emotion": {"type": "string"},
                "review_days": {"type": "integer"},
            },
            "required": ["topic", "decision"],
        },
    },
    "decision.review": {
        "description": "决策复盘",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "result": {"type": "string"},
                "feeling": {"type": "string"},
            },
            "required": ["result"],
        },
    },
    "decision.list": {
        "description": "查看待复盘决策",
        "parameters": {"type": "object", "properties": {}},
    },
    "voice.journal": {
        "description": "长语音(>200字)整理为结构化日记",
        "parameters": {
            "type": "object",
            "properties": {
                "asr_text": {"type": "string"},
                "attachment": {"type": "string"},
            },
            "required": ["asr_text"],
        },
    },
    "deep.dive": {
        "description": "主题深潜（跨时间线分析）",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "save": {"type": "boolean"},
            },
            "required": ["topic"],
        },
    },
    "internal.read": {
        "description": "[Agent] 读取文件内容",
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "max_chars": {"type": "integer"},
            },
            "required": ["paths"],
        },
    },
    "internal.search": {
        "description": "[Agent] 搜索笔记",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "string", "enum": ["quick_notes", "archives", "all"]},
                "max_results": {"type": "integer"},
            },
            "required": ["keywords"],
        },
    },
    "internal.list": {
        "description": "[Agent] 列出文件",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string"},
            },
            "required": ["directory"],
        },
    },
    "internal.grep": {
        "description": "[Agent] 递归搜索用户数据（支持正则），返回文件名+行号+上下文。比 internal.search 更强大，可搜索全部历史数据。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式（支持正则表达式）"},
                "path": {"type": "string", "description": "搜索起始路径（相对用户目录，默认全目录）"},
                "glob": {"type": "string", "description": "文件名过滤，如 *.md（默认 *.md）"},
                "context_lines": {"type": "integer", "description": "匹配行前后返回几行上下文（默认 1）"},
                "max_results": {"type": "integer", "description": "最大返回条数（默认 20）"},
                "case_sensitive": {"type": "boolean", "description": "是否区分大小写（默认 false）"},
            },
            "required": ["pattern"],
        },
    },
    "settings.nickname": {
        "description": "设置用户昵称（'叫我XX'触发）",
        "parameters": {
            "type": "object",
            "properties": {"nickname": {"type": "string"}},
            "required": ["nickname"],
        },
    },
    "settings.ai_name": {
        "description": "给AI起昵称（'叫你XX'触发）",
        "parameters": {
            "type": "object",
            "properties": {"ai_name": {"type": "string"}},
            "required": ["ai_name"],
        },
    },
    "settings.soul": {
        "description": "设置说话风格（mode: set/append/reset）",
        "parameters": {
            "type": "object",
            "properties": {
                "style": {"type": "string"},
                "mode": {"type": "string", "enum": ["set", "append", "reset"]},
            },
            "required": ["style"],
        },
    },
    "settings.info": {
        "description": "记录用户信息（category: occupation/city/pets/people/other）",
        "parameters": {
            "type": "object",
            "properties": {
                "info": {"type": "string"},
                "category": {"type": "string", "enum": ["occupation", "city", "pets", "people", "other"]},
            },
            "required": ["info"],
        },
    },
    "settings.skills": {
        "description": "管理功能开关（action: list/enable/disable）",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "enable", "disable"]},
                "skill_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["action"],
        },
    },
    "web.token": {
        "description": "生成Web查看链接",
        "parameters": {"type": "object", "properties": {}},
    },
    "dynamic": {
        "description": "通用状态操作（op: state.set/state.delete/state.push/file.write/file.append）。可操作: active_experiment.*/daily_top3/pending_decisions/custom.*。优先用专用skill，dynamic是兜底。",
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string"},
                            "path": {"type": "string"},
                            "value": {},
                        },
                        "required": ["op", "path"],
                    },
                },
            },
            "required": ["actions"],
        },
    },
    "reflect.push": {
        "description": "推送深度自问",
        "parameters": {"type": "object", "properties": {}},
    },
    "reflect.answer": {
        "description": "回答深度自问",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
    "reflect.skip": {
        "description": "跳过深度自问",
        "parameters": {"type": "object", "properties": {}},
    },
    "reflect.history": {
        "description": "查看深度自问历史",
        "parameters": {
            "type": "object",
            "properties": {"days": {"type": "integer"}},
        },
    },
    "finance.query": {
        "description": "查询收支（query_type: balance/expense/income/summary）",
        "parameters": {
            "type": "object",
            "properties": {
                "query_type": {"type": "string", "enum": ["balance", "expense", "income", "summary"]},
                "time_range": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["query_type"],
        },
    },
    "finance.snapshot": {
        "description": "财务快照",
        "parameters": {"type": "object", "properties": {}},
    },
    "finance.import": {
        "description": "导入财务数据",
        "parameters": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
        },
    },
    "finance.monthly": {
        "description": "月度财务报告",
        "parameters": {
            "type": "object",
            "properties": {"month": {"type": "string"}},
        },
    },
}


def build_tools(allowed_skill_names: list) -> list:
    """根据允许的 Skill 列表，生成 OpenAI tools 数组。

    Args:
        allowed_skill_names: 经过 visibility + 用户黑白名单过滤后的 skill name 列表

    Returns:
        OpenAI tools 格式的列表
    """
    tools = []
    for name in sorted(SKILL_SCHEMAS.keys()):
        if name not in allowed_skill_names:
            continue
        schema = SKILL_SCHEMAS[name]
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        })
    return tools


def build_tools_for_system_action(action: str) -> list:
    """系统任务使用的精简 tools 列表"""
    # 定时任务大多只需要少量 skill
    action_skills = {
        "morning_report": [],  # 早报不需要工具
        "evening_checkin": [],
        "daily_report": ["daily.generate"],
        "reflect_push": ["reflect.push"],
        "mood_generate": ["mood.generate"],
        "weekly_review": ["weekly.review"],
        "monthly_review": [],
    }
    names = action_skills.get(action, [])
    if not names:
        return []
    return build_tools(names)
