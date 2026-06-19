# -*- coding: utf-8 -*-
"""
Skill: finance.query
收支即时查询 — 用户随时问收支相关问题，返回结构化数据供 Flash 层生成回复。

V12 移植：handler 签名 (params, state, ctx)，IO 通过 ctx.IO。
"""
import sys
from finance_utils import (
    load_finance_data, filter_bills, summarize_bills,
    resolve_time_range, format_period, parse_amount, parse_date
)

def _log(msg):
    from logger import log
    log(msg)


def handle_query(params, state, ctx):
    """
    收支查询入口。

    params:
        query_type: expense|income|balance|detail  — 查询类型
        time_range: this_month|last_month|this_week|this_year|last_year|custom
        start_date: YYYY-MM-DD（custom 时）
        end_date: YYYY-MM-DD（custom 时）
        category: str — 一级分类名（可选）
        limit: int — detail 模式下返回记录数（默认 10）

    returns:
        {success, reply?, agent_context}
    """
    data = load_finance_data(ctx)
    if not data:
        return {"success": False, "reply": "还没有找到财务数据文件，请先把 finance_data.json 放到 03-Finance 目录~"}

    bills = data.get("data", {}).get("收支账单", [])
    if not bills:
        return {"success": False, "reply": "收支账单数据为空，还没有导入记录呢"}

    query_type = params.get("query_type", "balance")
    time_range = params.get("time_range", "this_month")
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    category = params.get("category")
    limit = int(params.get("limit", 10))

    # 解析时间范围
    start_dt, end_dt = resolve_time_range(time_range, start_date, end_date)
    period = format_period(start_dt, end_dt)

    # 按类型筛选
    bill_type = None
    if query_type == "expense":
        bill_type = "支出"
    elif query_type == "income":
        bill_type = "收入"

    filtered = filter_bills(bills, start_dt, end_dt, bill_type=bill_type, category=category)

    if not filtered:
        agent_ctx = {"period": period, "message": "该时间段内没有找到匹配的记录"}
        if category:
            agent_ctx["category"] = category
        return {"success": True, "agent_context": agent_ctx}

    # detail 模式：返回明细
    if query_type == "detail":
        detail_records = sorted(filtered, key=lambda b: b.get("日期", ""), reverse=True)[:limit]
        details = []
        for b in detail_records:
            details.append({
                "date": b.get("日期", ""),
                "type": b.get("类型", ""),
                "amount": parse_amount(b.get("金额")),
                "category": b.get("一级分类", ""),
                "sub_category": b.get("二级分类", ""),
                "note": b.get("备注", ""),
            })
        summary = summarize_bills(filtered)
        return {
            "success": True,
            "agent_context": {
                "period": period,
                "query_type": "detail",
                "category": category,
                "total_records": len(filtered),
                "showing": len(details),
                "details": details,
                "summary": summary,
            }
        }

    # 汇总模式
    summary = summarize_bills(filtered)

    # 计算日均支出
    days = max((end_dt - start_dt).days, 1)
    daily_avg = round(summary["total_expense"] / days, 2) if summary["total_expense"] > 0 else 0

    agent_ctx = {
        "period": period,
        "query_type": query_type or "balance",
        **summary,
        "daily_avg_expense": daily_avg,
        "days": days,
    }
    if category:
        agent_ctx["category"] = category

    _log(f"[finance.query] {period} | type={query_type} cat={category} | "
         f"expense={summary['total_expense']} income={summary['total_income']}")

    return {"success": True, "agent_context": agent_ctx}


# Skill 热加载注册表（V12: visibility=private，仅管理员可见可用）
SKILL_REGISTRY = {
    "finance.query": {
        "handler": handle_query,
        "visibility": "private",
        "description": "收支查询",
    },
}
