# -*- coding: utf-8 -*-
"""
Karvis 定时调度器（Event 函数）
通过定时触发器调用 Karvis Web 函数的 /system 端点。

部署为腾讯云 SCF Event 函数：
  入口函数：index.main_handler
  运行环境：Python 3.9
  超时时间：120 秒
  内存：64 MB

配置定时触发器（cron 为7位：秒 分 时 日 月 星期 年，日和星期互斥用?）：
  - 缓存刷新：  0 */30 * * * ? *        {"action":"refresh_cache"}
  - 晨报推送：  0 0 8 * * ? *           {"action":"morning_report"}
  - 晚间打卡：  0 0 21 * * ? *          {"action":"evening_checkin"}
  - 待办提醒：  0 0 9,14,18 * * ? *     {"action":"todo_remind"}
  - 轻推检测：  0 0 14 * * ? *          {"action":"nudge_check"}          (F5)
  - 情绪日记：  0 0 22 * * ? *          {"action":"mood_generate"}
  - 周回顾：    0 30 21 ? * 1 *         {"action":"weekly_review"}        (F4, 每周日21:30)
  - 日报生成：  0 30 22 * * ? *         {"action":"daily_report"}
  - 月度回顾：  0 0 22 L * ? *          {"action":"monthly_review"}       (F6, 每月最后一天22:00)
"""
import os
import json
import requests

# Karvis Web 函数的公网 URL（通过环境变量配置）
KARVIS_SYSTEM_URL = os.environ.get("KARVIS_SYSTEM_URL", "http://127.0.0.1:9000/system")


def main_handler(event, context):
    """SCF Event 函数入口"""
    print(f"[Scheduler] event={json.dumps(event, ensure_ascii=False)[:300]}")

    # 从定时触发器的附加信息(Message)中解析 action
    message = event.get("Message", "{}")
    try:
        payload = json.loads(message) if isinstance(message, str) else message
    except (json.JSONDecodeError, TypeError):
        payload = {}

    action = payload.get("action", "")
    if not action:
        print("[Scheduler] 无 action，跳过")
        return {"ok": False, "error": "no action in Message"}

    # 调用 Karvis /system 端点
    try:
        resp = requests.post(
            KARVIS_SYSTEM_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        print(f"[Scheduler] {action} -> {resp.status_code}: {resp.text[:200]}")
        return {"ok": True, "action": action, "status": resp.status_code}
    except Exception as e:
        print(f"[Scheduler] {action} 调用失败: {e}")
        return {"ok": False, "action": action, "error": str(e)}
