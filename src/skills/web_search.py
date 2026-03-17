# -*- coding: utf-8 -*-
"""
Skill: web.search
联网搜索 — 让 Karvis 能搜索互联网获取实时信息。

实现方式：
  腾讯云知识引擎 lkeap 原生联网搜索（enable_search=True）
  - 无需额外 API Key，复用已有的 DEEPSEEK_API_KEY
  - 无需额外依赖，复用已有的 requests
  - 搜索 + 回答一步完成，lkeap 内部调搜狗搜索引擎

工作流程：
  用户问实时问题 → LLM 决策 web.search → 带 enable_search 调 lkeap → 直接返回联网回答

支持场景：
  - "今天深圳天气怎么样"（实时天气）
  - "最近有什么好看的电影"（实时推荐）
  - "XXX 是什么"（知识查询）
  - "帮我查一下 Python 3.12 有什么新特性"（技术查询）
  - "最近的科技新闻"（新闻资讯）
"""
import sys
import requests
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))


def _log(msg):
    ts = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
    print(f"{ts} {msg}", file=sys.stderr, flush=True)


def execute(params, state, ctx):
    """
    web.search — 联网搜索（腾讯云 lkeap 原生能力）

    params:
        query: str — 搜索关键词/问题
        detailed: bool — 是否需要详细结果（默认 false）

    returns:
        {"success": bool, "reply": str}
    """
    from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    query = (params.get("query") or "").strip()
    detailed = params.get("detailed", False)

    if not query:
        return {"success": False, "reply": "想搜什么呢？告诉我你想查的内容~"}

    _log(f"[web.search] 开始联网搜索: query={query}")

    # ── 调用 lkeap DeepSeek API，开启 enable_search ──
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    # 构建 system prompt：引导模型基于搜索结果简洁回答
    system_msg = (
        "你是用户的 AI 助手，用户需要你联网查找信息。"
        "请根据搜索到的最新信息，用自然简洁的语言回答。"
        "回答要求：\n"
        "1. 直接回答，不要说"根据搜索结果"之类的话\n"
        "2. 信息准确，必要时标注来源\n"
        "3. 控制在 300 字以内，除非用户要求详细\n"
        "4. 口语化，像朋友聊天"
    )
    if detailed:
        system_msg += "\n5. 用户希望了解详细信息，可以多写一些，分点说明。"

    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": query},
        ],
        "enable_search": True,       # 🔑 腾讯云 lkeap 联网搜索开关
        "max_tokens": 800 if detailed else 500,
        "temperature": 0.3,
    }

    try:
        t0 = _now_ts()
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        elapsed = _now_ts() - t0

        if resp.status_code == 200:
            result = resp.json()
            answer = result["choices"][0]["message"]["content"].strip()

            # 提取搜索引用信息（如果有）
            search_results = result["choices"][0]["message"].get("search_results")
            source_hint = ""
            if search_results:
                sources = []
                for sr in search_results[:3]:
                    name = sr.get("name") or sr.get("title", "")
                    if name:
                        sources.append(name)
                if sources:
                    source_hint = "\n\n📎 参考：" + "、".join(sources)

            _log(f"[web.search] 联网回答完成: {elapsed:.1f}s, {len(answer)} chars"
                 f", 引用{len(search_results or [])}条")

            return {"success": True, "reply": answer + source_hint}

        _log(f"[web.search] API 错误: {resp.status_code} - {resp.text[:200]}")
        return {
            "success": False,
            "reply": "搜索出了点问题，稍后再试试~",
        }

    except requests.exceptions.Timeout:
        _log(f"[web.search] 请求超时")
        return {"success": False, "reply": "搜索超时了，网络可能不太好，再试一次？"}
    except Exception as e:
        _log(f"[web.search] 异常: {e}")
        return {"success": False, "reply": "搜索遇到问题了，稍后再试~"}


def _now_ts():
    import time
    return time.time()


# ============ Skill 热加载注册表 ============
SKILL_REGISTRY = {
    "web.search": execute,
}
