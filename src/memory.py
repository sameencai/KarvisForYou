# -*- coding: utf-8 -*-
"""
KarvisForAll 记忆管理（多用户版）
缓存按 user_id 分区，所有函数接收 UserContext。
"""
import time
import sys
import os
import copy
import threading
from config import RECENT_MESSAGES_LIMIT, PROMPT_CACHE_TTL, STATE_CACHE_TTL
import json as _json

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


# ============ Prompt 缓存（按 file_path，多用户天然隔离）============

_TMP_CACHE_DIR = "/tmp/karvis_prompts"

class PromptCache:
    """Memory 文件缓存：内存 → /tmp 磁盘 → 本地文件（三级缓存）"""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        os.makedirs(_TMP_CACHE_DIR, exist_ok=True)

    def _tmp_path(self, file_path):
        safe = file_path.replace("/", "_").replace(" ", "_").replace(os.sep, "_")
        return os.path.join(_TMP_CACHE_DIR, safe)

    def get(self, file_path, io=None):
        """读取文件内容（三级缓存）。io: 可选的存储 IO 对象，用于 L3 回源读取。"""
        now = time.time()

        # 1. 内存缓存
        cached = self._cache.get(file_path)
        if cached and cached["expire_time"] > now:
            return cached["content"]

        # 2. /tmp 磁盘缓存
        tmp_file = self._tmp_path(file_path)
        try:
            if os.path.exists(tmp_file):
                mtime = os.path.getmtime(tmp_file)
                if now - mtime < PROMPT_CACHE_TTL:
                    with open(tmp_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    with self._lock:
                        self._cache[file_path] = {"content": content, "expire_time": now + PROMPT_CACHE_TTL}
                    return content
        except Exception:
            pass

        # 3. 通过 IO 对象回源读取
        if io is None:
            from storage import create_storage
            io = create_storage()
        content = io.read_text(file_path)
        if content is not None:
            with self._lock:
                self._cache[file_path] = {"content": content, "expire_time": now + PROMPT_CACHE_TTL}
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
        return content or ""

    def invalidate(self, file_path=None):
        """清除缓存（全部或指定文件）"""
        with self._lock:
            if file_path:
                self._cache.pop(file_path, None)
                try:
                    tmp_file = self._tmp_path(file_path)
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception:
                    pass
            else:
                self._cache.clear()
                try:
                    for f in os.listdir(_TMP_CACHE_DIR):
                        os.remove(os.path.join(_TMP_CACHE_DIR, f))
                except Exception:
                    pass


_prompt_cache = PromptCache()


def load_memory(ctx):
    """加载某个用户的 memory.md"""
    return _prompt_cache.get(ctx.memory_file, io=ctx.IO)


# ============ 对话窗口管理 ============

def format_recent_messages(state):
    """从 state 中提取最近 N 条消息，格式化为文本。"""
    recent = state.get("recent_messages", [])[-RECENT_MESSAGES_LIMIT:]
    if not recent:
        return "（暂无最近对话）"

    lines = []
    for m in recent:
        role_val = m.get("role", "")
        if role_val == "system":
            lines.append(m.get("content", ""))
            continue
        role = "用户" if role_val == "user" else "Karvis"
        t = m.get("time", "")
        content = m.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"[{t}] {role}: {content}")
    return "\n".join(lines)


def add_message_to_state(state, role, content):
    """往 state 的对话窗口中追加一条消息。"""
    from datetime import datetime, timezone, timedelta
    beijing_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")

    messages = state.setdefault("recent_messages", [])
    messages.append({
        "role": role,
        "content": content[:1000],
        "time": now_str
    })

    if len(messages) > RECENT_MESSAGES_LIMIT:
        state["recent_messages"] = maybe_compress_messages(messages)


def maybe_compress_messages(messages):
    """对话压缩：保留最近 6 条原始消息，旧消息压缩为摘要（每条 100 字，总上限 800 字）。"""
    COMPRESS_KEEP_RECENT = 6  # 保留最近 6 条原始消息

    if len(messages) <= RECENT_MESSAGES_LIMIT:
        return messages

    to_compress = messages[:-COMPRESS_KEEP_RECENT]
    to_keep = messages[-COMPRESS_KEEP_RECENT:]

    summary_parts = []
    for m in to_compress:
        if m.get("role") == "system" and m.get("content", "").startswith("[对话摘要]"):
            summary_parts.append(m["content"])
            continue
        role = "用户" if m.get("role") == "user" else "Karvis"
        content = m.get("content", "")
        # 截取关键部分（保留足够语义）
        if len(content) > 200:
            content = content[:200] + "..."
        summary_parts.append(f"{role}: {content}")

    time_range = ""
    if to_compress:
        first_time = to_compress[0].get("time", "")
        last_time = to_compress[-1].get("time", "")
        if first_time and last_time:
            time_range = f"({first_time} ~ {last_time})"

    summary_text = f"[对话摘要] {time_range} " + " | ".join(summary_parts)
    if len(summary_text) > 1500:
        summary_text = summary_text[:1500] + "..."

    summary_msg = {
        "role": "system",
        "content": summary_text,
        "time": to_compress[-1].get("time", "") if to_compress else ""
    }

    result = [summary_msg] + to_keep
    _log(f"[记忆] 对话压缩: {len(to_compress)} 条 → 1 条摘要, 保留 {len(to_keep)} 条原始")
    return result


# ============ 长期记忆更新 ============

def apply_memory_updates(updates, ctx):
    """将 LLM 返回的 memory_updates 应用到该用户的 memory.md。"""
    if not updates:
        return

    memory_file = ctx.memory_file
    memory_text = ctx.IO.read_text(memory_file)
    if memory_text is None:
        _log(f"[记忆] 无法读取 memory.md ({ctx.user_id})，跳过记忆更新")
        return

    changed = False
    for item in updates:
        if isinstance(item, str):
            _log(f"[记忆] 跳过非法格式的 memory_update: {item[:50]}")
            continue
        if not isinstance(item, dict):
            continue
        section = item.get("section", "")
        action = item.get("action", "add")
        content = item.get("content", "")
        if not section or not content:
            continue

        section_header = f"## {section}"

        if action == "delete":
            if section_header not in memory_text:
                continue
            parts = memory_text.split(section_header, 1)
            before = parts[0]
            after = parts[1]
            next_idx = after.find("\n## ")
            section_body = after[:next_idx] if next_idx >= 0 else after
            rest = after[next_idx:] if next_idx >= 0 else ""
            keyword = content.lower()
            lines = section_body.split("\n")
            new_lines = [l for l in lines if keyword not in l.lower()]
            if len(new_lines) != len(lines):
                memory_text = before + section_header + "\n".join(new_lines) + rest
                changed = True
                _log(f"[记忆] 删除: section={section}, keyword={content}")
            continue

        if section_header in memory_text:
            if action == "add":
                dedup_key = content.split(":")[0].strip().lower() if ":" in content else content[:10].lower()
                parts = memory_text.split(section_header, 1)
                before = parts[0]
                after = parts[1]
                next_idx = after.find("\n## ")
                section_body = after[:next_idx] if next_idx >= 0 else after
                rest = after[next_idx:] if next_idx >= 0 else ""

                existing_lines = section_body.lower()
                if dedup_key in existing_lines:
                    _log(f"[记忆] 去重跳过: section={section}, key={dedup_key}")
                    continue

                memory_text = before + section_header + section_body.rstrip() + f"\n- {content}\n" + rest
                changed = True
            elif action == "update":
                parts = memory_text.split(section_header, 1)
                before = parts[0]
                after = parts[1]
                next_idx = after.find("\n## ")
                if next_idx >= 0:
                    rest = after[next_idx:]
                    memory_text = before + section_header + f"\n- {content}\n" + rest
                else:
                    memory_text = before + section_header + f"\n- {content}\n"
                changed = True
        else:
            memory_text = memory_text.rstrip() + f"\n\n{section_header}\n- {content}\n"
            changed = True

    if changed:
        ok = ctx.IO.write_text(memory_file, memory_text)
        if ok:
            _log(f"[记忆] memory.md 已更新 ({ctx.user_id}): {len(updates)} 条")
            _prompt_cache.invalidate(memory_file)
        else:
            _log(f"[记忆] memory.md 写入失败 ({ctx.user_id})")


# ============ State 缓存（按 user_id 分区）============

# {user_id: {"data": dict, "expire_time": float}}
_state_cache = {}
_state_lock = threading.Lock()


def read_state_cached(ctx):
    """读取某个用户的 state（带缓存）。"""
    uid = ctx.user_id
    now = time.time()

    # 1. 内存缓存
    with _state_lock:
        cached = _state_cache.get(uid)
        if cached and cached["data"] is not None and cached["expire_time"] > now:
            _log(f"[State] 命中内存缓存 ({uid})")
            return copy.deepcopy(cached["data"])

    # 2. /tmp 磁盘缓存
    tmp_file = os.path.join(_TMP_CACHE_DIR, f"_state_{uid}.json")
    try:
        if os.path.exists(tmp_file):
            mtime = os.path.getmtime(tmp_file)
            if now - mtime < STATE_CACHE_TTL:
                with open(tmp_file, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                with _state_lock:
                    _state_cache[uid] = {"data": data, "expire_time": now + STATE_CACHE_TTL}
                _log(f"[State] 命中 /tmp 缓存 ({uid})")
                return copy.deepcopy(data)
    except Exception:
        pass

    # 3. 通过 IO 回源读取
    data = ctx.IO.read_json(ctx.state_file) or {}
    _update_state_cache(uid, data)
    _log(f"[State] 从文件读取 ({uid})")
    return copy.deepcopy(data)


def _update_state_cache(uid, state):
    """更新某用户 state 的内存和 /tmp 缓存"""
    now = time.time()
    with _state_lock:
        _state_cache[uid] = {"data": state, "expire_time": now + STATE_CACHE_TTL}
    try:
        tmp_file = os.path.join(_TMP_CACHE_DIR, f"_state_{uid}.json")
        os.makedirs(_TMP_CACHE_DIR, exist_ok=True)
        with open(tmp_file, "w", encoding="utf-8") as f:
            _json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def write_state_and_update_cache(state, ctx):
    """写入某用户的 state 并更新缓存"""
    ctx.IO.write_json(ctx.state_file, state)
    _update_state_cache(ctx.user_id, state)


def invalidate_all_caches():
    """清除所有缓存（定时任务用）"""
    _prompt_cache.invalidate()
    with _state_lock:
        _state_cache.clear()
    _log("[Memory] 全部缓存已清除")
