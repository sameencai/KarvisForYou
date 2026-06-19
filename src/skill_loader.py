# -*- coding: utf-8 -*-
"""
KarvisForAll V12 Skill 热加载器
handler 签名: (params, state, ctx) -> dict

V12 改造：支持 visibility 字段和元数据注册。
SKILL_REGISTRY 支持两种格式：
  旧格式: {"skill.name": handler_fn}  → visibility 默认 "public"
  新格式: {"skill.name": {"handler": handler_fn, "visibility": "private", "description": "...", ...}}
"""
import os
import sys
import importlib
from datetime import datetime, timezone, timedelta

_BEIJING_TZ = timezone(timedelta(hours=8))

def _log(msg):
    from logger import log
    log(msg)


_cached_registry = None   # {name: handler}
_cached_metadata = None    # {name: {visibility, description, preview_message, ...}}


def _normalize_entry(skill_name, entry):
    """将 SKILL_REGISTRY 条目标准化为 (handler, metadata) 格式"""
    if callable(entry):
        # 旧格式: handler_fn
        return entry, {"visibility": "public"}
    elif isinstance(entry, dict) and "handler" in entry:
        # 新格式: {"handler": fn, "visibility": "private", ...}
        handler = entry["handler"]
        meta = {k: v for k, v in entry.items() if k != "handler"}
        meta.setdefault("visibility", "public")
        return handler, meta
    else:
        _log(f"[SkillLoader] 警告: skill '{skill_name}' 注册格式异常，跳过")
        return None, None


def load_skill_registry():
    """扫描 skills/ 目录，合并所有模块的 SKILL_REGISTRY。

    返回: dict[str, callable] — skill_name → handler_fn(params, state, ctx)
    """
    global _cached_registry, _cached_metadata
    if _cached_registry is not None:
        return _cached_registry

    registry = {}
    metadata = {}
    skills_dir = os.path.join(os.path.dirname(__file__), "skills")
    loaded_modules = []

    for filename in sorted(os.listdir(skills_dir)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        module_name = f"skills.{filename[:-3]}"
        try:
            mod = importlib.import_module(module_name)
            mod_registry = getattr(mod, "SKILL_REGISTRY", None)
            if mod_registry and isinstance(mod_registry, dict):
                for skill_name, entry in mod_registry.items():
                    handler, meta = _normalize_entry(skill_name, entry)
                    if handler is None:
                        continue
                    if skill_name in registry:
                        _log(f"[SkillLoader] 警告: skill '{skill_name}' 重复注册，"
                             f"来自 {module_name}，覆盖已有")
                    registry[skill_name] = handler
                    metadata[skill_name] = meta
                loaded_modules.append(filename[:-3])
        except Exception as e:
            _log(f"[SkillLoader] 加载 {module_name} 失败: {e}")

    # 内置 ignore handler
    registry["ignore"] = lambda params, state, ctx: {"success": True}
    metadata["ignore"] = {"visibility": "public"}

    _log(f"[SkillLoader] 已加载 {len(loaded_modules)} 个模块, 共 {len(registry)} 个 skill")
    _cached_registry = registry
    _cached_metadata = metadata
    return registry


def get_skill_metadata():
    """获取所有 Skill 的元数据（visibility 等）。

    返回: dict[str, dict] — skill_name → {visibility, description, preview_message, ...}
    """
    global _cached_metadata
    if _cached_metadata is None:
        load_skill_registry()
    return _cached_metadata or {}


def get_visible_skills(ctx) -> dict:
    """获取对指定用户可见的 Skill handler 字典。

    三层过滤：
      1. visibility 过滤（private → 仅 admin）
      2. preview → 所有人可见但不可执行（不从此函数过滤，由执行层处理）
      3. ctx.is_skill_allowed() → 用户级黑白名单

    返回: dict[str, callable] — 过滤后的 skill_name → handler
    """
    registry = load_skill_registry()
    metadata = get_skill_metadata()

    visible = {}
    for name, handler in registry.items():
        meta = metadata.get(name, {})
        vis = meta.get("visibility", "public")

        # private → 仅 admin
        if vis == "private" and not ctx.is_admin:
            continue

        # 用户级黑白名单
        if not ctx.is_skill_allowed(name):
            continue

        visible[name] = handler

    return visible


def get_skills_for_prompt(ctx) -> list:
    """获取应注入到 Prompt 中的 Skill 列表（用于动态生成 SKILLS 文本）。

    返回列表中的每个元素是 skill_name 字符串。
    preview 类 Skill 不注入 Prompt（LLM 不需要知道不可执行的功能）。

    返回: list[str]
    """
    registry = load_skill_registry()
    metadata = get_skill_metadata()

    result = []
    for name in registry:
        meta = metadata.get(name, {})
        vis = meta.get("visibility", "public")

        # private → 仅 admin
        if vis == "private" and not ctx.is_admin:
            continue

        # preview → 不注入 Prompt
        if vis == "preview":
            continue

        # 用户级黑白名单
        if not ctx.is_skill_allowed(name):
            continue

        result.append(name)

    return result
