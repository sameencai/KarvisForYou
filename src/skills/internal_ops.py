# -*- coding: utf-8 -*-
"""
Skill: internal_ops (V3-F10)
内部操作工具 — 为 Agent Loop 提供文件读取和搜索能力。

工作方式：
1. brain.py 的 Agent Loop 检测到 LLM 返回 continue=true 时启动多轮循环
2. LLM 可选择 internal.read / internal.search 等 skill 获取更多信息
3. skill 执行结果作为新的 context 再次喂给 LLM
4. 循环直到 LLM 返回 continue=false 或达到最大轮数（5轮）

安全约束：
- 只能读取 OBSIDIAN_BASE 下的文件
- 写操作不在此模块（Agent Loop 的写操作需另外确认）
- 每次读取限制返回内容长度
"""
import sys
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def read_files(params, state, ctx):
    """
    internal.read — 读取指定文件内容。

    params:
        paths: list[str] — 要读取的文件路径列表（相对于 OBSIDIAN_BASE）
        max_chars: int — 每个文件最大返回字符数（默认 1000）
    """
    from storage import IO as OneDriveIO
    from concurrent.futures import ThreadPoolExecutor

    paths = params.get("paths", [])
    max_chars = params.get("max_chars", 1000)

    if not paths:
        return {"success": False, "reply": "没有指定要读取的文件路径"}

    # 安全检查：只允许读取用户 base_dir 下的文件
    full_paths = []
    for p in paths[:5]:  # 最多 5 个文件
        if p.startswith("/"):
            if not p.startswith(ctx.base_dir):
                _log(f"[internal_ops] 安全拒绝: {p}")
                continue
            full_paths.append(p)
        else:
            full_paths.append(f"{ctx.base_dir}/{p}")

    if not full_paths:
        return {"success": False, "reply": "没有合法的文件路径"}

    # 并发读取
    try:
        from brain import _executor
        executor = _executor
    except Exception:
        executor = ThreadPoolExecutor(max_workers=4)

    futures = {p: executor.submit(OneDriveIO.read_text, p) for p in full_paths}

    results = {}
    for p, fut in futures.items():
        try:
            content = fut.result(timeout=15) or ""
            # 截断
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n...(截断，共 {len(content)} 字符)"
            results[p] = content
        except Exception as e:
            results[p] = f"读取失败: {e}"

    _log(f"[internal_ops] 读取 {len(results)} 个文件")

    # 返回 agent_context（供 Agent Loop 使用）
    return {
        "success": True,
        "reply": None,  # 不直接回复用户
        "agent_context": results,
    }


def search_files(params, state, ctx):
    """
    internal.search — 在笔记中搜索关键词。

    params:
        keywords: list[str] — 搜索关键词
        scope: str — 搜索范围："quick_notes" | "archives" | "all"（默认 all）
        max_results: int — 最大返回条数（默认 10）
    """
    from storage import IO as OneDriveIO
    from concurrent.futures import ThreadPoolExecutor

    keywords = params.get("keywords", [])
    scope = params.get("scope", "all")
    max_results = params.get("max_results", 10)

    if not keywords:
        return {"success": False, "reply": "没有指定搜索关键词"}

    keyword_lower = [kw.lower() for kw in keywords]

    try:
        from brain import _executor
        executor = _executor
    except Exception:
        executor = ThreadPoolExecutor(max_workers=6)

    files_to_read = {}
    if scope in ("quick_notes", "all"):
        files_to_read["quick_notes"] = ctx.quick_notes_file
        files_to_read["misc"] = ctx.misc_file

    if scope in ("archives", "all"):
        today = datetime.now(BEIJING_TZ).date()
        for i in range(14):  # 最近 14 天的归档
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            files_to_read[f"emotion_{d}"] = f"{ctx.emotion_notes_dir}/{d}.md"
            files_to_read[f"work_{d}"] = f"{ctx.work_notes_dir}/{d}.md"
            files_to_read[f"fun_{d}"] = f"{ctx.fun_notes_dir}/{d}.md"

    futures = {k: executor.submit(OneDriveIO.read_text, v) for k, v in files_to_read.items()}
    results_text = {}
    for k, fut in futures.items():
        try:
            results_text[k] = fut.result(timeout=15) or ""
        except Exception:
            results_text[k] = ""

    # 搜索匹配
    matches = []
    for source, text in results_text.items():
        if not text:
            continue
        for para in text.split("\n\n"):
            if any(kw in para.lower() for kw in keyword_lower):
                matches.append({
                    "source": source,
                    "content": para.strip()[:200]
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    _log(f"[internal_ops] 搜索完成: {len(matches)} 条匹配")

    return {
        "success": True,
        "reply": None,
        "agent_context": {
            "matches": matches,
            "total": len(matches),
            "keywords": keywords,
        }
    }


def list_files(params, state, ctx):
    """
    internal.list — 列出指定目录下的文件（仅名称）。

    params:
        directory: str — 目录路径（相对于 OBSIDIAN_BASE）
    """
    from storage import IO as OneDriveIO

    directory = params.get("directory", "")
    if not directory:
        return {"success": False, "reply": "没有指定目录路径"}

    if directory.startswith("/"):
        if not directory.startswith(ctx.base_dir):
            return {"success": False, "reply": "不允许访问该目录"}
        full_path = directory
    else:
        full_path = f"{ctx.base_dir}/{directory}"

    # 本地文件列表
    import os
    try:
        items = os.listdir(full_path)
        file_list = [
            {"name": item, "type": "folder" if os.path.isdir(os.path.join(full_path, item)) else "file"}
            for item in sorted(items)[:30]  # 最多 30 个
        ]
        return {
            "success": True,
            "reply": None,
            "agent_context": {"directory": full_path, "files": file_list}
        }
    except Exception as e:
        _log(f"[internal_ops] list 异常: {e}")
        return {"success": False, "reply": f"目录读取异常: {e}"}


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "internal.read": read_files,
    "internal.search": search_files,
    "internal.list": list_files,
}
