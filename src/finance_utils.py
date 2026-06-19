# -*- coding: utf-8 -*-
"""
财务数据公共工具
供 finance_query / finance_snapshot / finance_import / finance_report 共用。

V12 移植：所有 IO 操作通过 ctx.IO + ctx.finance_data_file 进行。
"""
import sys
import re
from datetime import datetime, timezone, timedelta

def _log(msg):
    from logger import log
    log(msg)

# ---- 北京时区 ----
_BJ_TZ = timezone(timedelta(hours=8))

def now_bj():
    """返回北京时间的 datetime"""
    return datetime.now(_BJ_TZ)

# ---- 请求级缓存（同一次调用内复用） ----
_finance_cache = {"data": None, "ts": 0}
_CACHE_TTL = 60  # 秒


def load_finance_data(ctx, force=False):
    """读取 finance_data.json，带请求级缓存。

    Args:
        ctx: UserContext — 提供 IO 后端和路径
        force: bool — 是否强制刷新缓存

    Returns:
        dict（完整 JSON），失败返回 None
    """
    import time
    now = time.time()
    if not force and _finance_cache["data"] and (now - _finance_cache["ts"]) < _CACHE_TTL:
        return _finance_cache["data"]

    _log(f"[finance] 读取 {ctx.finance_data_file}")
    data = ctx.IO.read_json(ctx.finance_data_file)
    if data is None:
        _log("[finance] 读取失败")
        return None
    if not data:
        _log("[finance] 文件不存在或为空")
        return None

    _finance_cache["data"] = data
    _finance_cache["ts"] = now
    return data


def save_finance_data(ctx, data):
    """写回 finance_data.json

    Args:
        ctx: UserContext — 提供 IO 后端和路径
        data: dict — 完整 JSON 数据
    """
    data["lastModified"] = now_bj().isoformat()
    ok = ctx.IO.write_json(ctx.finance_data_file, data)
    if ok:
        _finance_cache["data"] = data
        import time
        _finance_cache["ts"] = time.time()
    return ok


# ============================================================
# 日期解析工具
# ============================================================

def parse_date(s):
    """将多种日期格式统一解析为 datetime 对象。

    支持格式：
      - 2026/01/21 14:36:00
      - 2026-01-21 14:36:00
      - 2026-01-21
      - 2026/1/21
      - 2025/7/4
    """
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_amount(v):
    """将金额字段统一转为 float（支持字符串和数字）"""
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


# ============================================================
# 收支统计工具
# ============================================================

def filter_bills(bills, start_date=None, end_date=None, bill_type=None, category=None):
    """筛选收支账单记录。

    Args:
        bills: list[dict] — 收支账单数组
        start_date: datetime — 开始日期（含）
        end_date: datetime — 结束日期（含）
        bill_type: str — "支出" 或 "收入"
        category: str — 一级分类名

    Returns:
        list[dict] — 筛选后的记录
    """
    result = []
    for b in bills:
        dt = parse_date(b.get("日期"))
        if dt is None:
            continue
        if start_date and dt < start_date:
            continue
        if end_date and dt > end_date:
            continue
        if bill_type and b.get("类型") != bill_type:
            continue
        if category and b.get("一级分类") != category:
            continue
        result.append(b)
    return result


def summarize_bills(bills):
    """汇总收支统计。

    返回:
        dict: {
            total_expense, total_income, balance, savings_rate,
            expense_by_category, income_by_category,
            record_count, expense_count, income_count
        }
    """
    total_expense = 0.0
    total_income = 0.0
    expense_by_cat = {}
    income_by_cat = {}

    for b in bills:
        amount = parse_amount(b.get("金额"))
        cat = b.get("一级分类", "未分类")
        if b.get("类型") == "支出":
            exp = abs(amount)
            total_expense += exp
            expense_by_cat[cat] = expense_by_cat.get(cat, 0) + exp
        elif b.get("类型") == "收入":
            inc = abs(amount)
            total_income += inc
            income_by_cat[cat] = income_by_cat.get(cat, 0) + inc

    # 按金额降序排列
    expense_top = sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True)
    income_top = sorted(income_by_cat.items(), key=lambda x: x[1], reverse=True)

    balance = total_income - total_expense
    savings_rate = (balance / total_income * 100) if total_income > 0 else 0

    return {
        "total_expense": round(total_expense, 2),
        "total_income": round(total_income, 2),
        "balance": round(balance, 2),
        "savings_rate": f"{savings_rate:.1f}%",
        "expense_by_category": [
            {"category": c, "amount": round(a, 2),
             "percent": f"{a/total_expense*100:.1f}%" if total_expense > 0 else "0%"}
            for c, a in expense_top
        ],
        "income_by_category": [
            {"category": c, "amount": round(a, 2)}
            for c, a in income_top
        ],
        "record_count": len(bills),
        "expense_count": sum(1 for b in bills if b.get("类型") == "支出"),
        "income_count": sum(1 for b in bills if b.get("类型") == "收入"),
    }


# ============================================================
# 资产快照工具
# ============================================================

# 净资产计算排除项（不可自由支配）— 基于 subCategory = '长期锁定'
_ILLIQUID_SUB_CATEGORY = "长期锁定"
# 兼容旧数据的 name 匹配（fallback）
_ILLIQUID_NAMES = {"公积金", "个人养老金", "待归属股票", "社保"}

def normalize_date_str(s):
    """将日期字符串标准化为 YYYY-MM-DD 格式。

    处理 '2026/1/6' → '2026-01-06'，'2026-01-24' → '2026-01-24' 等。
    """
    dt = parse_date(s)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return s  # 无法解析时原样返回


def group_snapshots_by_date(snapshots):
    """将快照记录按 updateDate 分组。

    日期先标准化为 YYYY-MM-DD，确保排序正确。
    返回: dict[str, list[dict]] — {date_str: [items...]}，按日期降序
    """
    groups = {}
    for item in snapshots:
        d = item.get("updateDate", "")
        if d:
            normalized = normalize_date_str(d)
            groups.setdefault(normalized, []).append(item)
    return dict(sorted(groups.items(), reverse=True))


def calc_snapshot_summary(items):
    """计算一期快照的资产汇总。

    口径说明：
    - 总资产 = Σ(所有资产) + Σ(所有负债)，负债金额已是负数，直接相加
    - 净资产 = 总资产 - Σ(subCategory=="长期锁定" 的资产，如 RSU/公积金)
    - 可支配资产 = 净资产（同义）

    返回: dict — {
        total_assets, total_liabilities, net_assets, disposable_assets,
        illiquid_assets, debt_ratio, by_asset_class, by_channel
    }
    """
    asset_sum = 0.0       # 资产类合计（正数）
    liability_sum = 0.0   # 负债类合计（负数原值）
    illiquid_total = 0.0  # 长期锁定资产
    by_asset_class = {}
    by_channel = {}

    for item in items:
        amount = parse_amount(item.get("amount"))
        cat = item.get("category", "")
        asset_class = item.get("assetClass", "其他")
        channel = item.get("channel", "其他")
        name = item.get("name", "")
        sub_category = item.get("subCategory", "")

        if cat == "资产":
            asset_sum += amount
            # 判断是否长期锁定（不可支配）
            if sub_category == _ILLIQUID_SUB_CATEGORY or (
                not sub_category and (name in _ILLIQUID_NAMES or any(n in name for n in _ILLIQUID_NAMES))
            ):
                illiquid_total += amount
        elif cat == "负债":
            liability_sum += amount  # 保留负数原值

        by_asset_class[asset_class] = by_asset_class.get(asset_class, 0) + amount
        by_channel[channel] = by_channel.get(channel, 0) + amount

    # 总资产 = 资产 + 负债（负债已是负数）
    total_assets = asset_sum + liability_sum
    # 净资产 = 总资产 - 长期锁定
    net_assets = total_assets - illiquid_total
    # 负债率 = |负债| / 资产
    debt_ratio = (abs(liability_sum) / asset_sum * 100) if asset_sum > 0 else 0

    return {
        "total_assets": round(total_assets, 2),
        "total_liabilities": round(liability_sum, 2),
        "net_assets": round(net_assets, 2),
        "disposable_assets": round(net_assets, 2),  # 同义保留，兼容下游
        "illiquid_assets": round(illiquid_total, 2),
        "debt_ratio": f"{debt_ratio:.1f}%",
        "by_asset_class": {k: round(v, 2) for k, v in
                           sorted(by_asset_class.items(), key=lambda x: abs(x[1]), reverse=True)},
        "by_channel": {k: round(v, 2) for k, v in
                       sorted(by_channel.items(), key=lambda x: abs(x[1]), reverse=True)},
    }


def compare_snapshots(current_items, previous_items):
    """对比两期快照，计算变动。

    返回: dict — {
        current, previous,
        asset_change, asset_change_pct,
        disposable_change, disposable_change_pct,
        class_changes: [{class, current, previous, change, change_pct}]
    }
    """
    curr = calc_snapshot_summary(current_items)
    prev = calc_snapshot_summary(previous_items)

    def _change(c, p):
        diff = c - p
        pct = (diff / abs(p) * 100) if p != 0 else 0
        return round(diff, 2), f"{pct:+.1f}%"

    asset_chg, asset_pct = _change(curr["total_assets"], prev["total_assets"])
    net_chg, net_pct = _change(curr["net_assets"], prev["net_assets"])

    # 按 assetClass 对比
    all_classes = set(curr["by_asset_class"]) | set(prev["by_asset_class"])
    class_changes = []
    for cls in all_classes:
        c_val = curr["by_asset_class"].get(cls, 0)
        p_val = prev["by_asset_class"].get(cls, 0)
        chg, pct = _change(c_val, p_val)
        if chg != 0:
            class_changes.append({
                "class": cls, "current": round(c_val, 2), "previous": round(p_val, 2),
                "change": chg, "change_pct": pct
            })
    class_changes.sort(key=lambda x: abs(x["change"]), reverse=True)

    return {
        "current": curr,
        "previous": prev,
        "asset_change": asset_chg,
        "asset_change_pct": asset_pct,
        "net_change": net_chg,
        "net_change_pct": net_pct,
        "disposable_change": net_chg,       # 兼容下游
        "disposable_change_pct": net_pct,    # 兼容下游
        "class_changes": class_changes,
    }


# ============================================================
# 时间范围解析
# ============================================================

def resolve_time_range(time_range, start_date=None, end_date=None):
    """将 LLM 传入的 time_range 转为 (start_datetime, end_datetime)。

    返回 naive datetime（无时区），与 parse_date() 保持一致，避免比较报错。

    time_range 取值：this_month, last_month, this_week, this_year, last_year, custom
    custom 时使用 start_date / end_date 参数（字符串 YYYY-MM-DD）
    """
    # 用北京时间取"现在"，但返回 naive datetime
    now = now_bj().replace(tzinfo=None)

    if time_range == "this_month":
        s = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        e = now
    elif time_range == "last_month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        e = first_this - timedelta(seconds=1)
        s = e.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "this_week":
        weekday = now.weekday()  # Monday=0
        s = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = now
    elif time_range == "this_year":
        s = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        e = now
    elif time_range == "last_year":
        s = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(year=now.year - 1, month=12, day=31, hour=23, minute=59, second=59)
    elif time_range == "custom" and start_date:
        s = parse_date(start_date) or now.replace(day=1)
        e = parse_date(end_date) if end_date else now
        # 如果 end_date 只有日期部分，扩展到当天结束
        if e and e.hour == 0 and e.minute == 0:
            e = e.replace(hour=23, minute=59, second=59)
    else:
        # 默认当月
        s = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        e = now

    return s, e


def format_currency(amount):
    """格式化金额显示"""
    return f"¥{amount:,.2f}"


def format_period(start, end):
    """根据时间范围生成可读的周期描述"""
    if start.year == end.year and start.month == end.month:
        return f"{start.year}年{start.month}月"
    elif start.year == end.year:
        return f"{start.year}年{start.month}月-{end.month}月"
    else:
        return f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"
