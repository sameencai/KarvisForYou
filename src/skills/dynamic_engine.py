# -*- coding: utf-8 -*-
"""
Skill: dynamic — 动态能力引擎 (V6)

让 LLM 直接操作 state 和文件，不需要预定义 skill 函数。
当现有硬编码 skill 无法覆盖用户意图时，LLM 可以自由编排原子操作。

支持的操作:
  state.set    — 设置 state 中某个字段的值
  state.delete — 删除 state 中某个字段
  state.push   — 往 state 中的数组追加一项
  file.read    — 读取文件（结果放入 reply 上下文）
  file.write   — 写入文件
  file.append  — 追加内容到文件

安全机制:
  - state 操作有字段白名单
  - file 操作有目录白名单
  - 单次最多 10 个 action
"""
import sys
import copy
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============ 安全白名单 ============

# state 中允许动态操作的顶层字段
_STATE_WHITELIST = {
    "active_experiment",
    "experiment_history",
    "daily_top3",
    "active_book",
    "active_media",
    "pending_decisions",
    "decision_history",
    "nudge_state",
    "custom",            # 预留：LLM 自创的任意数据都放这里
}

# file 操作允许的目录前缀（相对用户数据根目录）
_FILE_WRITE_PREFIXES = (
    "02-Notes/",
    "_Karvis/",
)

_FILE_READ_PREFIXES = (
    "02-Notes/",
    "01-DailyNotes/",
    "_Karvis/",
)

_MAX_ACTIONS = 10


# ============ 路径操作工具 ============

def _resolve_path(obj, path):
    """将 'a.b.c' 解析为嵌套字典访问，返回 (parent, key) 或 (None, None)"""
    keys = path.split(".")
    current = obj
    for k in keys[:-1]:
        if isinstance(current, dict):
            if k not in current:
                current[k] = {}
            current = current[k]
        elif isinstance(current, list):
            try:
                current = current[int(k)]
            except (ValueError, IndexError):
                return None, None
        else:
            return None, None
    return current, keys[-1]


def _check_state_path(path):
    """检查 state path 是否在白名单内"""
    top_key = path.split(".")[0]
    return top_key in _STATE_WHITELIST


def _check_file_path(path, prefixes):
    """检查文件路径是否在允许的目录内"""
    return any(path.startswith(p) for p in prefixes)


# ============ 原子操作实现 ============

def _op_state_set(state, action, ctx=None):
    path = action.get("path", "")
    value = action.get("value")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_state_path(path):
        return {"ok": False, "error": f"字段 '{path}' 不在可操作范围内"}

    parent, key = _resolve_path(state, path)
    if parent is None:
        return {"ok": False, "error": f"路径 '{path}' 无法解析"}

    parent[key] = value
    return {"ok": True}


def _op_state_delete(state, action, ctx=None):
    path = action.get("path", "")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_state_path(path):
        return {"ok": False, "error": f"字段 '{path}' 不在可操作范围内"}

    parent, key = _resolve_path(state, path)
    if parent is None or not isinstance(parent, dict):
        return {"ok": False, "error": f"路径 '{path}' 无法解析"}

    parent.pop(key, None)
    return {"ok": True}


def _op_state_push(state, action, ctx=None):
    path = action.get("path", "")
    value = action.get("value")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_state_path(path):
        return {"ok": False, "error": f"字段 '{path}' 不在可操作范围内"}

    parent, key = _resolve_path(state, path)
    if parent is None:
        return {"ok": False, "error": f"路径 '{path}' 无法解析"}

    arr = parent.get(key)
    if arr is None:
        parent[key] = [value]
    elif isinstance(arr, list):
        arr.append(value)
        # 数组保护：最多保留 50 项
        if len(arr) > 50:
            parent[key] = arr[-50:]
    else:
        return {"ok": False, "error": f"'{path}' 不是数组"}

    return {"ok": True}


def _op_file_read(state, action, ctx=None):
    path = action.get("path", "")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_file_path(path, _FILE_READ_PREFIXES):
        return {"ok": False, "error": f"无权读取 '{path}'"}

    try:
        from storage import IO
        # 多用户：拼接用户数据根目录
        full_path = path
        if ctx and hasattr(ctx, 'obsidian_base'):
            import os
            full_path = os.path.join(ctx.obsidian_base, path)
        content = IO.read_text(full_path)
        if content is None:
            return {"ok": False, "error": f"读取失败: {path}"}
        # 截断防止过长
        if len(content) > 5000:
            content = content[:5000] + "\n...(已截断)"
        return {"ok": True, "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _op_file_write(state, action, ctx=None):
    path = action.get("path", "")
    content = action.get("content", "")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_file_path(path, _FILE_WRITE_PREFIXES):
        return {"ok": False, "error": f"无权写入 '{path}'"}

    try:
        from storage import IO
        full_path = path
        if ctx and hasattr(ctx, 'obsidian_base'):
            import os
            full_path = os.path.join(ctx.obsidian_base, path)
        ok = IO.write_text(full_path, content)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _op_file_append(state, action, ctx=None):
    path = action.get("path", "")
    content = action.get("content", "")
    if not path:
        return {"ok": False, "error": "缺少 path"}
    if not _check_file_path(path, _FILE_WRITE_PREFIXES):
        return {"ok": False, "error": f"无权写入 '{path}'"}

    try:
        from storage import IO
        full_path = path
        if ctx and hasattr(ctx, 'obsidian_base'):
            import os
            full_path = os.path.join(ctx.obsidian_base, path)
        existing = IO.read_text(full_path) or ""
        new_content = existing + content
        ok = IO.write_text(full_path, new_content)
        return {"ok": ok}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============ 操作分发表 ============

_OP_HANDLERS = {
    "state.set": _op_state_set,
    "state.delete": _op_state_delete,
    "state.push": _op_state_push,
    "file.read": _op_file_read,
    "file.write": _op_file_write,
    "file.append": _op_file_append,
}


# ============ 主入口 ============

def execute(params, state, ctx=None):
    """
    dynamic skill 主函数。

    params:
        actions: list[dict] — 原子操作数组
            每个 action: {"op": "state.set", "path": "...", "value": ...}
    """
    actions = params.get("actions", [])
    if not actions:
        return {"success": False, "reply": "没有指定操作"}

    if len(actions) > _MAX_ACTIONS:
        _log(f"[dynamic] 操作数 {len(actions)} 超过上限 {_MAX_ACTIONS}，截断")
        actions = actions[:_MAX_ACTIONS]

    results = []
    errors = []
    for i, action in enumerate(actions):
        op = action.get("op", "")
        handler = _OP_HANDLERS.get(op)
        if not handler:
            err = f"未知操作: {op}"
            _log(f"[dynamic] action[{i}] {err}")
            errors.append(err)
            continue

        result = handler(state, action, ctx=ctx)
        results.append({"op": op, "path": action.get("path", ""), "result": result})

        if not result.get("ok"):
            err = f"{op}({action.get('path', '')}): {result.get('error', '未知错误')}"
            _log(f"[dynamic] action[{i}] 失败: {err}")
            errors.append(err)
        else:
            _log(f"[dynamic] action[{i}] OK: {op} → {action.get('path', '')}")

    success = len(errors) == 0
    if not success:
        _log(f"[dynamic] 执行完成，{len(errors)} 个错误: {errors}")

    return {
        "success": success,
        "results": results,
        "errors": errors if errors else None,
    }


# ============ Skill 注册表 ============
SKILL_REGISTRY = {
    "dynamic": execute,
}
