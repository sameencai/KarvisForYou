# -*- coding: utf-8 -*-
"""
Skill: finance.snapshot
资产快照查询 — 用户查询当前总资产、净资产、各类别资产、历史对比等。

V12 移植：handler 签名 (params, state, ctx)，IO 通过 ctx.IO。
"""
import sys
from finance_utils import (
    load_finance_data, group_snapshots_by_date,
    calc_snapshot_summary, compare_snapshots, parse_amount
)

def _log(msg):
    from logger import log
    log(msg)


def handle_snapshot(params, state, ctx):
    """
    资产快照查询入口。

    params:
        query_type: summary|compare|by_category|by_channel|trend
        category: str — 按 subCategory 筛选
        channel: str — 按 channel 筛选

    returns:
        {success, reply?, agent_context}
    """
    data = load_finance_data(ctx)
    if not data:
        return {"success": False, "reply": "还没有找到财务数据文件，请先把 finance_data.json 放到 03-Finance 目录~"}

    snapshots = data.get("data", {}).get("资产快照", [])
    if not snapshots:
        return {"success": False, "reply": "暂无资产快照数据"}

    query_type = params.get("query_type", "summary")
    cat_filter = params.get("category")
    channel_filter = params.get("channel")

    # 按日期分组
    grouped = group_snapshots_by_date(snapshots)
    dates = list(grouped.keys())  # 已按日期降序

    if not dates:
        return {"success": False, "reply": "资产快照数据为空"}

    latest_date = dates[0]
    latest_items = grouped[latest_date]

    # ---- summary: 最新一期汇总 ----
    if query_type == "summary":
        summary = calc_snapshot_summary(latest_items)
        agent_ctx = {
            "query_type": "summary",
            "snapshot_date": latest_date,
            "total_snapshots": len(dates),
            **summary,
        }
        _log(f"[finance.snapshot] summary | date={latest_date} | "
             f"total={summary['total_assets']} net={summary['net_assets']} illiquid={summary['illiquid_assets']}")
        return {"success": True, "agent_context": agent_ctx}

    # ---- compare: 最近两期对比 ----
    if query_type == "compare":
        if len(dates) < 2:
            summary = calc_snapshot_summary(latest_items)
            return {
                "success": True,
                "agent_context": {
                    "query_type": "compare",
                    "snapshot_date": latest_date,
                    "message": "只有一期快照数据，无法对比",
                    **summary,
                }
            }
        prev_date = dates[1]
        prev_items = grouped[prev_date]
        comparison = compare_snapshots(latest_items, prev_items)
        agent_ctx = {
            "query_type": "compare",
            "current_date": latest_date,
            "previous_date": prev_date,
            **comparison,
        }
        _log(f"[finance.snapshot] compare | {prev_date} → {latest_date} | "
             f"asset_chg={comparison['asset_change']}")
        return {"success": True, "agent_context": agent_ctx}

    # ---- by_category: 按 subCategory 查询 ----
    if query_type == "by_category" and cat_filter:
        filtered = [i for i in latest_items if i.get("subCategory") == cat_filter]
        if not filtered:
            return {
                "success": True,
                "agent_context": {
                    "query_type": "by_category",
                    "category": cat_filter,
                    "snapshot_date": latest_date,
                    "message": f"没有找到「{cat_filter}」类别的资产数据",
                    "available_categories": list(set(i.get("subCategory", "") for i in latest_items)),
                }
            }
        items_detail = []
        total = 0.0
        for i in filtered:
            amt = parse_amount(i.get("amount"))
            total += amt
            items_detail.append({
                "name": i.get("name", ""),
                "channel": i.get("channel", ""),
                "asset_class": i.get("assetClass", ""),
                "amount": round(amt, 2),
            })
        items_detail.sort(key=lambda x: abs(x["amount"]), reverse=True)
        return {
            "success": True,
            "agent_context": {
                "query_type": "by_category",
                "category": cat_filter,
                "snapshot_date": latest_date,
                "total": round(total, 2),
                "items": items_detail,
            }
        }

    # ---- by_channel: 按渠道查询 ----
    if query_type == "by_channel" and channel_filter:
        filtered = [i for i in latest_items if i.get("channel") == channel_filter]
        if not filtered:
            return {
                "success": True,
                "agent_context": {
                    "query_type": "by_channel",
                    "channel": channel_filter,
                    "snapshot_date": latest_date,
                    "message": f"没有找到「{channel_filter}」渠道的数据",
                    "available_channels": list(set(i.get("channel", "") for i in latest_items)),
                }
            }
        items_detail = []
        total = 0.0
        for i in filtered:
            amt = parse_amount(i.get("amount"))
            total += amt
            items_detail.append({
                "name": i.get("name", ""),
                "category": i.get("category", ""),
                "sub_category": i.get("subCategory", ""),
                "asset_class": i.get("assetClass", ""),
                "amount": round(amt, 2),
            })
        items_detail.sort(key=lambda x: abs(x["amount"]), reverse=True)
        return {
            "success": True,
            "agent_context": {
                "query_type": "by_channel",
                "channel": channel_filter,
                "snapshot_date": latest_date,
                "total": round(total, 2),
                "items": items_detail,
            }
        }

    # ---- trend: 历史趋势（最近 6 期总资产/净资产） ----
    if query_type == "trend":
        trend_data = []
        for d in dates[:6]:
            s = calc_snapshot_summary(grouped[d])
            trend_data.append({
                "date": d,
                "total_assets": s["total_assets"],
                "net_assets": s["net_assets"],
                "total_liabilities": s["total_liabilities"],
            })
        trend_data.reverse()  # 按时间正序
        return {
            "success": True,
            "agent_context": {
                "query_type": "trend",
                "periods": len(trend_data),
                "trend": trend_data,
            }
        }

    # 默认 fallback: summary
    summary = calc_snapshot_summary(latest_items)
    return {
        "success": True,
        "agent_context": {
            "query_type": "summary",
            "snapshot_date": latest_date,
            **summary,
        }
    }


# Skill 热加载注册表（V12: visibility=private，仅管理员可见可用）
SKILL_REGISTRY = {
    "finance.snapshot": {
        "handler": handle_snapshot,
        "visibility": "private",
        "description": "资产快照查询",
    },
}
