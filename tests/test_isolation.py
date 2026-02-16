# -*- coding: utf-8 -*-
"""
KarvisForAll 数据隔离测试
模拟多用户场景，验证：
1. 用户目录结构独立
2. 用户注册表正确记录
3. 令牌系统：生成/验证/过期/清理
4. 消息计数与限流
5. 用户挂起/激活
6. Web API 鉴权隔离（用户 A 的 token 不能访问用户 B 的数据）
7. 并发安全

运行方式:
    cd KarvisForAll/src && python -m pytest ../tests/test_isolation.py -v
    或:
    cd KarvisForAll && python tests/test_isolation.py
"""
import os
import sys
import json
import shutil
import tempfile
import threading
import time

# 让 import 能找到 src/
_src_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
sys.path.insert(0, _src_dir)

# 使用临时目录作为 DATA_DIR，避免污染真实数据
_test_data_dir = tempfile.mkdtemp(prefix="karvis_test_")
os.environ["DATA_DIR"] = _test_data_dir
os.environ["ADMIN_TOKEN"] = "test-admin-token-12345"
os.environ["DAILY_MESSAGE_LIMIT"] = "5"
os.environ["WEB_TOKEN_EXPIRE_HOURS"] = "1"

# 现在才 import 项目模块（依赖 DATA_DIR 环境变量）
from user_context import (
    get_or_create_user, get_all_active_users, get_all_users,
    increment_message_count, is_user_suspended,
    update_user_status, update_user_nickname,
    generate_token, verify_token, cleanup_expired_tokens,
    DATA_DIR, SYSTEM_DIR, UserContext,
)


# ============================================================
# 测试工具
# ============================================================

_pass_count = 0
_fail_count = 0


def _assert(condition, msg=""):
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
        print(f"  ✓ {msg}")
    else:
        _fail_count += 1
        print(f"  ✗ FAIL: {msg}")


def _section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# 测试 1: 用户目录结构隔离
# ============================================================

def test_user_directory_isolation():
    _section("测试 1: 用户目录结构隔离")

    ctx_a, is_new_a = get_or_create_user("user_alice")
    ctx_b, is_new_b = get_or_create_user("user_bob")
    ctx_c, is_new_c = get_or_create_user("user_charlie")

    _assert(is_new_a, "Alice 是新用户")
    _assert(is_new_b, "Bob 是新用户")
    _assert(is_new_c, "Charlie 是新用户")

    # 再次获取不应是新用户
    ctx_a2, is_new_a2 = get_or_create_user("user_alice")
    _assert(not is_new_a2, "Alice 第二次获取不是新用户")

    # 目录独立
    _assert(ctx_a.base_dir != ctx_b.base_dir, "Alice 和 Bob 的 base_dir 不同")
    _assert(ctx_b.base_dir != ctx_c.base_dir, "Bob 和 Charlie 的 base_dir 不同")

    # 目录结构存在
    for name, ctx in [("Alice", ctx_a), ("Bob", ctx_b), ("Charlie", ctx_c)]:
        _assert(os.path.isdir(ctx.inbox_path), f"{name} inbox 目录存在")
        _assert(os.path.isdir(ctx.daily_notes_dir), f"{name} daily 目录存在")
        _assert(os.path.isdir(ctx.book_notes_dir), f"{name} book_notes 目录存在")
        _assert(os.path.isfile(ctx.quick_notes_file), f"{name} Quick-Notes 文件存在")
        _assert(os.path.isfile(ctx.todo_file), f"{name} Todo 文件存在")
        _assert(os.path.isfile(ctx.state_file), f"{name} state 文件存在")
        _assert(os.path.isfile(ctx.memory_file), f"{name} memory 文件存在")
        _assert(os.path.isfile(ctx.user_config_file), f"{name} user_config 文件存在")

    # 写入数据到 Alice，确认 Bob 看不到
    with open(ctx_a.quick_notes_file, "a", encoding="utf-8") as f:
        f.write("\n## 2026-02-16 10:00 Alice 的私密笔记\n\n这是 Alice 的笔记内容\n\n---\n")

    bob_notes = ""
    with open(ctx_b.quick_notes_file, "r", encoding="utf-8") as f:
        bob_notes = f.read()
    _assert("Alice" not in bob_notes, "Bob 的 Quick-Notes 中没有 Alice 的内容")

    alice_notes = ""
    with open(ctx_a.quick_notes_file, "r", encoding="utf-8") as f:
        alice_notes = f.read()
    _assert("Alice 的私密笔记" in alice_notes, "Alice 的 Quick-Notes 中有她的内容")


# ============================================================
# 测试 2: 用户注册表
# ============================================================

def test_user_registry():
    _section("测试 2: 用户注册表")

    all_users = get_all_users()
    _assert("user_alice" in all_users, "注册表包含 Alice")
    _assert("user_bob" in all_users, "注册表包含 Bob")
    _assert("user_charlie" in all_users, "注册表包含 Charlie")
    _assert(len(all_users) == 3, f"注册表共 3 个用户 (实际: {len(all_users)})")

    alice_data = all_users["user_alice"]
    _assert(alice_data["status"] == "active", "Alice 状态为 active")
    _assert("created_at" in alice_data, "Alice 有 created_at")
    _assert("last_active" in alice_data, "Alice 有 last_active")
    _assert(alice_data["total_messages"] == 0, "Alice 初始消息数为 0")


# ============================================================
# 测试 3: 令牌系统
# ============================================================

def test_token_system():
    _section("测试 3: 令牌系统")

    # 生成令牌
    token_a = generate_token("user_alice")
    token_b = generate_token("user_bob")
    _assert(token_a != token_b, "Alice 和 Bob 的令牌不同")
    _assert(len(token_a) == 36, f"令牌格式为 UUID (长度={len(token_a)})")

    # 验证令牌
    result_a = verify_token(token_a)
    _assert(result_a["valid"], "Alice 的令牌有效")
    _assert(result_a["user_id"] == "user_alice", "Alice 的令牌指向 Alice")

    result_b = verify_token(token_b)
    _assert(result_b["valid"], "Bob 的令牌有效")
    _assert(result_b["user_id"] == "user_bob", "Bob 的令牌指向 Bob")

    # 无效令牌
    result_invalid = verify_token("fake-token-12345")
    _assert(not result_invalid["valid"], "假令牌验证失败")

    result_empty = verify_token("")
    _assert(not result_empty["valid"], "空令牌验证失败")

    # 过期令牌
    token_short = generate_token("user_charlie", expire_hours=0)  # 立即过期
    # 等待一点时间确保过期
    time.sleep(0.1)
    result_expired = verify_token(token_short)
    _assert(not result_expired["valid"], "过期令牌验证失败")
    _assert(result_expired.get("expired", False), "过期令牌标记为 expired")

    # 清理过期令牌
    removed = cleanup_expired_tokens()
    _assert(removed >= 1, f"清理了 {removed} 个过期令牌")

    # 有效令牌仍然可用
    result_a2 = verify_token(token_a)
    _assert(result_a2["valid"], "清理后 Alice 的令牌仍然有效")


# ============================================================
# 测试 4: 消息计数与限流
# ============================================================

def test_message_count_limit():
    _section("测试 4: 消息计数与限流")

    # DAILY_MESSAGE_LIMIT = 5
    for i in range(5):
        count, over = increment_message_count("user_alice")
        _assert(not over, f"Alice 第 {count} 条消息未超限")

    # 第 6 条超限
    count, over = increment_message_count("user_alice")
    _assert(over, f"Alice 第 {count} 条消息超限")
    _assert(count == 6, f"Alice 消息数为 6 (实际: {count})")

    # Bob 不受 Alice 影响
    count_b, over_b = increment_message_count("user_bob")
    _assert(not over_b, "Bob 的消息不受 Alice 限流影响")
    _assert(count_b == 1, f"Bob 消息数为 1 (实际: {count_b})")

    # 验证总消息数
    all_users = get_all_users()
    _assert(all_users["user_alice"]["total_messages"] == 6,
            f"Alice 总消息数为 6 (实际: {all_users['user_alice']['total_messages']})")


# ============================================================
# 测试 5: 用户挂起/激活
# ============================================================

def test_user_suspend_activate():
    _section("测试 5: 用户挂起/激活")

    _assert(not is_user_suspended("user_charlie"), "Charlie 初始状态未挂起")

    update_user_status("user_charlie", "suspended")
    _assert(is_user_suspended("user_charlie"), "Charlie 被挂起后状态为 suspended")

    # 挂起用户不在活跃列表中
    active = get_all_active_users()
    _assert("user_charlie" not in active, "挂起用户 Charlie 不在活跃列表中")
    _assert("user_alice" in active, "Alice 仍在活跃列表中")

    # 重新激活
    update_user_status("user_charlie", "active")
    _assert(not is_user_suspended("user_charlie"), "Charlie 重新激活后未挂起")

    active2 = get_all_active_users()
    _assert("user_charlie" in active2, "Charlie 重新激活后在活跃列表中")


# ============================================================
# 测试 6: 昵称设置
# ============================================================

def test_nickname():
    _section("测试 6: 昵称设置")

    ctx_a, _ = get_or_create_user("user_alice")
    _assert(ctx_a.get_nickname() == "", "Alice 初始没有昵称")

    # 设置昵称
    config = ctx_a.get_user_config()
    config["nickname"] = "小爱"
    ctx_a.save_user_config(config)
    _assert(ctx_a.get_nickname() == "小爱", "Alice 昵称设置为小爱")

    # 更新注册表中的昵称
    update_user_nickname("user_alice", "小爱")
    all_users = get_all_users()
    _assert(all_users["user_alice"]["nickname"] == "小爱", "注册表中 Alice 昵称为小爱")

    # Bob 的昵称不受影响
    ctx_b, _ = get_or_create_user("user_bob")
    _assert(ctx_b.get_nickname() == "", "Bob 仍然没有昵称")


# ============================================================
# 测试 7: 并发安全
# ============================================================

def test_concurrent_safety():
    _section("测试 7: 并发安全")

    errors = []

    def worker(uid, count):
        try:
            for _ in range(count):
                ctx, _ = get_or_create_user(uid)
                increment_message_count(uid)
                generate_token(uid)
        except Exception as e:
            errors.append(f"{uid}: {e}")

    threads = []
    for uid in ["user_alice", "user_bob", "user_charlie"]:
        t = threading.Thread(target=worker, args=(uid, 10))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=30)

    _assert(len(errors) == 0, f"并发操作无异常 (errors={errors})")

    # 验证注册表完整性
    all_users = get_all_users()
    _assert(len(all_users) == 3, f"并发后仍然只有 3 个用户 (实际: {len(all_users)})")


# ============================================================
# 测试 8: Web API 鉴权隔离（不启动 Flask，直接测试装饰器逻辑）
# ============================================================

def test_token_cross_user():
    _section("测试 8: 令牌交叉隔离")

    token_a = generate_token("user_alice")
    token_b = generate_token("user_bob")

    # Alice 的 token 不能标识为 Bob
    result_a = verify_token(token_a)
    _assert(result_a["user_id"] == "user_alice", "Alice 的令牌标识为 Alice（非 Bob）")

    result_b = verify_token(token_b)
    _assert(result_b["user_id"] == "user_bob", "Bob 的令牌标识为 Bob（非 Alice）")

    # 确认路径隔离
    ctx_a = UserContext(result_a["user_id"])
    ctx_b = UserContext(result_b["user_id"])
    _assert(ctx_a.base_dir != ctx_b.base_dir, "通过令牌获取的用户路径不同")
    _assert("user_alice" in ctx_a.base_dir, "Alice 的路径包含 user_alice")
    _assert("user_bob" in ctx_b.base_dir, "Bob 的路径包含 user_bob")


# ============================================================
# 测试 9: Flask 端到端 API 测试
# ============================================================

def test_flask_api_e2e():
    _section("测试 9: Flask 端到端 API 测试")

    from flask import Flask
    from web_routes import web_bp, api_bp

    app = Flask(__name__)
    app.register_blueprint(web_bp, url_prefix="/web")
    app.register_blueprint(api_bp, url_prefix="/api")

    client = app.test_client()

    # 生成令牌
    token_a = generate_token("user_alice")
    token_b = generate_token("user_bob")

    # --- 验证令牌 API ---
    resp = client.post("/api/auth/verify", json={"token": token_a})
    _assert(resp.status_code == 200, "验证令牌 API 返回 200")
    data = resp.get_json()
    _assert(data["valid"], "Alice 的令牌验证成功")
    _assert(data["user_id"] == "user_alice", "返回正确的 user_id")

    # --- 无 token 请求 ---
    resp = client.get("/api/dashboard")
    _assert(resp.status_code == 401, "无 token 访问 dashboard 返回 401")

    # --- 假 token 请求 ---
    resp = client.get("/api/dashboard", headers={"X-Token": "fake-token"})
    _assert(resp.status_code == 401, "假 token 访问 dashboard 返回 401")

    # --- Alice 访问 dashboard ---
    resp = client.get("/api/dashboard", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 dashboard 返回 200")
    data = resp.get_json()
    _assert("nickname" in data, "dashboard 包含 nickname")
    _assert("date" in data, "dashboard 包含 date")

    # --- Alice 访问 notes ---
    resp = client.get("/api/notes", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 notes 返回 200")
    data = resp.get_json()
    _assert("notes" in data, "notes 返回包含 notes 字段")
    # Alice 之前写过一条笔记
    _assert(data["total"] >= 1, f"Alice 至少有 1 条速记 (实际: {data['total']})")

    # --- Bob 看不到 Alice 的笔记 ---
    resp = client.get("/api/notes", headers={"X-Token": token_b})
    data = resp.get_json()
    has_alice = any("Alice" in n.get("content", "") for n in data.get("notes", []))
    _assert(not has_alice, "Bob 的 notes 中不包含 Alice 的内容")

    # --- todos ---
    resp = client.get("/api/todos", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 todos 返回 200")

    # --- daily ---
    resp = client.get("/api/daily", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 daily 返回 200")

    # --- archive ---
    resp = client.get("/api/archive", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 archive 返回 200")

    # --- mood ---
    resp = client.get("/api/mood", headers={"X-Token": token_a})
    _assert(resp.status_code == 200, "Alice 访问 mood 返回 200")

    # --- 管理员 API ---
    resp = client.get("/api/admin/users")
    _assert(resp.status_code == 403, "无 admin token 访问管理 API 返回 403")

    resp = client.get("/api/admin/users",
                       headers={"X-Admin-Token": "test-admin-token-12345"})
    _assert(resp.status_code == 200, "正确 admin token 访问用户列表返回 200")
    data = resp.get_json()
    _assert("user_alice" in data.get("users", {}), "管理员可以看到 Alice")

    # --- 管理员挂起 ---
    resp = client.post("/api/admin/users/user_bob/suspend",
                        headers={"X-Admin-Token": "test-admin-token-12345"})
    _assert(resp.status_code == 200, "挂起 Bob 返回 200")
    _assert(is_user_suspended("user_bob"), "Bob 被挂起")

    # --- 管理员激活 ---
    resp = client.post("/api/admin/users/user_bob/activate",
                        headers={"X-Admin-Token": "test-admin-token-12345"})
    _assert(resp.status_code == 200, "激活 Bob 返回 200")
    _assert(not is_user_suspended("user_bob"), "Bob 重新激活")

    # --- Web 页面路由 ---
    for page in ["/web/", "/web/login", "/web/dashboard", "/web/notes",
                 "/web/todos", "/web/daily", "/web/archive", "/web/mood", "/web/admin"]:
        resp = client.get(page)
        _assert(resp.status_code in [200, 302],
                f"页面 {page} 返回 {resp.status_code}")


# ============================================================
# 运行所有测试
# ============================================================

def main():
    global _pass_count, _fail_count

    print(f"\nKarvisForAll 数据隔离测试")
    print(f"测试数据目录: {_test_data_dir}")
    print(f"DATA_DIR: {DATA_DIR}")

    test_user_directory_isolation()
    test_user_registry()
    test_token_system()
    test_message_count_limit()
    test_user_suspend_activate()
    test_nickname()
    test_concurrent_safety()
    test_token_cross_user()
    test_flask_api_e2e()

    # 清理测试数据
    print(f"\n清理测试数据: {_test_data_dir}")
    shutil.rmtree(_test_data_dir, ignore_errors=True)

    # 汇总
    _section("测试结果汇总")
    total = _pass_count + _fail_count
    print(f"  通过: {_pass_count}/{total}")
    print(f"  失败: {_fail_count}/{total}")

    if _fail_count > 0:
        print(f"\n  ✗ 存在失败的测试！")
        sys.exit(1)
    else:
        print(f"\n  ✓ 全部通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
