# -*- coding: utf-8 -*-
"""
Skill: book.*
读书笔记系统：创建书籍笔记、添加摘录/感想、AI 总结/金句。
"""
import sys
from datetime import datetime, timezone, timedelta
from storage import IO as OneDriveIO

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _now_str():
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _book_file(name, ctx):
    return f"{ctx.book_notes_dir}/{name}.md"


def _book_list_file(ctx):
    return f"{ctx.book_notes_dir}/_书单.md"


def create(params, state, ctx):
    """
    创建或切换书籍笔记。

    params:
        name: str — 书名
        author: str — 作者（LLM 填写）
        category: str — 分类（LLM 填写）
        description: str — 简介（LLM 填写）
        thought: str — 可选，首条感想
    """
    name = (params.get("name") or "").strip()
    if not name:
        return {"success": False, "reply": "需要书名"}

    author = (params.get("author") or "未知").strip()
    category = (params.get("category") or "未知").strip()
    description = (params.get("description") or "").strip()
    first_thought = (params.get("thought") or "").strip()

    file_path = _book_file(name, ctx)

    # 检查是否已存在
    existing = OneDriveIO.read_text(file_path)
    if existing is None:
        return {"success": False, "reply": "OneDrive 读取失败"}

    if not existing.strip():
        # 创建新笔记
        template = f"""---
type: book
title: {name}
author: {author}
category: {category}
start_date: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}
status: reading
tags: [读书, {category}]
---

# 📚 {name}

## 📋 基本信息

- **作者**：{author}
- **分类**：{category}
- **简介**：{description or '暂无'}
- **开始阅读**：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}

---

## ✂️ 摘录

---

## 💡 我的思考

---

## 💎 可分享的金句

---

## 🤖 AI 总结

---
"""
        if first_thought:
            template = template.replace(
                "## 💡 我的思考\n\n---",
                f"## 💡 我的思考\n\n{first_thought}\n*— {_now_str()}*\n\n---"
            )

        ok = OneDriveIO.write_text(file_path, template)
        if not ok:
            return {"success": False, "reply": "创建笔记失败"}

        # 更新书单索引
        _update_book_list(name, author, category, ctx)
        _log(f"[book.create] 新建: {name}")
    else:
        _log(f"[book.create] 切换到已有: {name}")
        # 文件已存在 + 有感想 → 自动转调 book.thought
        if first_thought:
            _log(f"[book.create] 已有笔记且携带感想，转调 book.thought")
            thought_result = thought({"content": first_thought, "book": name}, state, ctx)
            thought_result.setdefault("state_updates", {})["active_book"] = name
            return thought_result

    # 更新 state 中的活跃书籍
    return {
        "success": True,
        "state_updates": {"active_book": name}
    }


def excerpt(params, state, ctx):
    """
    添加书摘。

    params:
        content: str — 摘录内容
        book: str — 可选，指定书名（默认用 active_book）
    """
    content = (params.get("content") or "").strip()
    if not content:
        return {"success": False, "reply": "摘录内容不能为空"}

    book = (params.get("book") or state.get("active_book", "")).strip()
    if not book:
        return {"success": False, "reply": "还没有在读的书，先说一下书名吧"}

    entry = f"> {content}\n*— {_now_str()}*\n"
    ok = OneDriveIO.append_to_section(_book_file(book, ctx), "## ✂️ 摘录", entry)

    if ok:
        _log(f"[book.excerpt] 添加到 {book}")
        return {"success": True}
    else:
        return {"success": False, "reply": f"写入《{book}》失败"}


def thought(params, state, ctx):
    """
    添加读书感想。

    params:
        content: str — 感想内容
        book: str — 可选，指定书名
    """
    content = (params.get("content") or "").strip()
    if not content:
        return {"success": False, "reply": "感想不能为空"}

    book = (params.get("book") or state.get("active_book", "")).strip()
    if not book:
        return {"success": False, "reply": "还没有在读的书，先说一下书名吧"}

    entry = f"{content}\n*— {_now_str()}*\n"
    ok = OneDriveIO.append_to_section(_book_file(book, ctx), "## 💡 我的思考", entry)

    if ok:
        _log(f"[book.thought] 添加到 {book}")
        return {"success": True}
    else:
        return {"success": False, "reply": f"写入《{book}》失败"}


def summary(params, state, ctx):
    """
    AI 生成读书总结。

    params:
        book: str — 可选，指定书名
    """
    book = (params.get("book") or state.get("active_book", "")).strip()
    if not book:
        return {"success": False, "reply": "需要指定书名"}

    content = OneDriveIO.read_text(_book_file(book, ctx))
    if not content or not content.strip():
        return {"success": False, "reply": f"没找到《{book}》的笔记"}

    from brain import call_deepseek
    import json
    import prompts

    prompt = prompts.get("BOOK_SUMMARY_USER", book=book, content=content[:3000])

    response = call_deepseek([
        {"role": "system", "content": prompts.BOOK_SUMMARY_SYSTEM},
        {"role": "user", "content": prompt}
    ], max_tokens=800, temperature=0.7)

    if not response:
        return {"success": False, "reply": "AI 分析失败"}

    # 解析 + 写入
    analysis = _parse_json(response)
    if not analysis:
        return {"success": False, "reply": "AI 分析结果解析失败"}

    summary_md = f"""### 📖 核心观点
{analysis.get('core_ideas', '')}

### 🧠 思考脉络
{analysis.get('thinking_path', '')}

### 📚 关联阅读
{analysis.get('recommendations', '')}

### 💬 一句话总结
{analysis.get('one_liner', '')}

*🤖 AI 生成于 {_now_str()}*
"""
    ok = OneDriveIO.append_to_section(_book_file(book, ctx), "## 🤖 AI 总结", summary_md)

    if ok:
        one_liner = analysis.get("one_liner", "总结已生成")
        return {"success": True, "reply": f"《{book}》总结已生成\n💬 {one_liner}"}
    else:
        return {"success": False, "reply": "写入总结失败"}


def quotes(params, state, ctx):
    """
    AI 从摘录中提炼金句。

    params:
        book: str — 可选，指定书名
    """
    book = (params.get("book") or state.get("active_book", "")).strip()
    if not book:
        return {"success": False, "reply": "需要指定书名"}

    content = OneDriveIO.read_text(_book_file(book, ctx))
    if not content or not content.strip():
        return {"success": False, "reply": f"没找到《{book}》的笔记"}

    from brain import call_deepseek
    import json
    import prompts

    prompt = prompts.get("BOOK_QUOTES_USER", book=book, content=content[:3000])

    response = call_deepseek([
        {"role": "system", "content": prompts.BOOK_QUOTES_SYSTEM},
        {"role": "user", "content": prompt}
    ], max_tokens=500, temperature=0.8)

    if not response:
        return {"success": False, "reply": "AI 提炼失败"}

    quotes_list = _parse_json(response)
    if not isinstance(quotes_list, list):
        return {"success": False, "reply": "金句提炼结果解析失败"}

    # 写入笔记
    quotes_md = "\n".join([f"- {q}" for q in quotes_list])
    quotes_md += f"\n\n*🤖 AI 提炼于 {_now_str()}*\n"
    OneDriveIO.append_to_section(_book_file(book, ctx), "## 💎 可分享的金句", quotes_md)

    # 直接在回复中展示（方便复制）
    reply_lines = [f"《{book}》金句:"]
    for i, q in enumerate(quotes_list, 1):
        reply_lines.append(f"{i}. {q}")
    return {"success": True, "reply": "\n".join(reply_lines)}


def _update_book_list(name, author, category, ctx):
    """更新书单索引"""
    existing = OneDriveIO.read_text(_book_list_file(ctx)) or ""
    if not existing.strip():
        existing = "# 📚 书单\n\n| 书名 | 作者 | 分类 | 状态 | 日期 |\n|------|------|------|------|------|\n"

    date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    new_row = f"| [[{name}]] | {author} | {category} | 📖 在读 | {date} |"
    new_content = existing.rstrip() + "\n" + new_row + "\n"
    OneDriveIO.write_text(_book_list_file(ctx), new_content)


def _parse_json(text):
    """容错 JSON 解析"""
    import json
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{") if "{" in text else text.find("[")
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    return None


# Skill 热加载注册表（O-010）
SKILL_REGISTRY = {
    "book.create": create,
    "book.excerpt": excerpt,
    "book.thought": thought,
    "book.summary": summary,
    "book.quotes": quotes,
}
