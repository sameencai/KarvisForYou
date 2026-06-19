# -*- coding: utf-8 -*-
"""
Gateway Mode 集成测试 — 验证 Function Calling 模式的端到端流程。
运行方式: USE_GATEWAY=1 python3 tests/test_gateway.py
"""
import os
import sys
import json

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_tool_schema():
    """测试 tools 生成"""
    from tool_schema import build_tools, SKILL_SCHEMAS

    # 全量 tools
    all_tools = build_tools(list(SKILL_SCHEMAS.keys()))
    assert len(all_tools) > 40, f"Expected >40 tools, got {len(all_tools)}"

    # 过滤后的 tools
    subset = build_tools(["todo.add", "todo.done", "todo.list"])
    assert len(subset) == 3, f"Expected 3 tools, got {len(subset)}"

    # 验证格式
    for tool in all_tools:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params

    print("✅ test_tool_schema passed")


def test_gateway_result():
    """测试 GatewayResult 类"""
    from gateway import GatewayResult

    # 空结果
    r = GatewayResult()
    assert r.reply == ""
    assert r.primary_skill == "ignore"
    assert r.has_tool_calls == False

    # 有工具调用
    r = GatewayResult(
        reply="已添加待办~",
        executed_skills=[("todo.add", {"content": "买咖啡"}, {"success": True, "reply": "已添加"})],
        memory_updates=[{"section": "偏好", "action": "add", "content": "喜欢咖啡"}],
    )
    assert r.primary_skill == "todo.add"
    assert r.has_tool_calls == True
    assert len(r.memory_updates) == 1

    # 向后兼容 decision dict
    d = r.to_decision_dict()
    assert d["skill"] == "todo.add"
    assert d["reply"] == "已添加待办~"

    print("✅ test_gateway_result passed")


def test_build_gateway_system_prompt():
    """测试 Gateway 系统 prompt 转换"""
    from brain import _build_gateway_system_prompt
    import prompts

    # 模拟一个包含旧格式的 system prompt
    old_prompt = f"""# Karvis 灵魂

## 你是谁
你是 Karvis。

# 可用 Skill（参数均为 JSON）

- **todo.add** `{{content}}` — 添加待办
- **ignore** `{{reason?}}` — 不处理

{prompts.OUTPUT_FORMAT}
"""
    new_prompt = _build_gateway_system_prompt(old_prompt)

    # 应该移除 OUTPUT_FORMAT
    assert "严格 JSON" not in new_prompt, "Should remove OUTPUT_FORMAT"
    # 应该移除 SKILLS 段
    assert "可用 Skill（参数均为 JSON）" not in new_prompt, "Should remove SKILLS block"
    # 应该添加工具使用说明
    assert "工具使用说明" in new_prompt, "Should add gateway instructions"
    # 应该保留 SOUL
    assert "你是 Karvis" in new_prompt

    print("✅ test_build_gateway_system_prompt passed")


def test_format_tool_result():
    """测试工具结果格式化"""
    from gateway import _format_tool_result

    # 普通结果
    r = _format_tool_result({"success": True, "reply": "已添加"})
    data = json.loads(r)
    assert data["success"] == True
    assert data["reply"] == "已添加"

    # agent_context（internal 工具）
    r = _format_tool_result({"success": True, "agent_context": {"files": ["a.md", "b.md"]}})
    data = json.loads(r)
    assert "files" in data

    # 非 dict 结果
    r = _format_tool_result("ok")
    data = json.loads(r)
    assert data["success"] == True

    print("✅ test_format_tool_result passed")


def test_check_and_execute():
    """测试工具权限检查和执行"""
    from gateway import _check_and_execute

    # Mock ctx
    class MockCtx:
        is_admin = False
        user_id = "test_user"
        def is_skill_allowed(self, name):
            return True

    ctx = MockCtx()

    # ignore skill
    result = _check_and_execute("ignore", {}, {}, {}, {}, ctx)
    assert result["success"] == True

    # 未知 skill
    result = _check_and_execute("unknown.skill", {}, {}, {}, {}, ctx)
    assert result["success"] == False
    assert "未知" in result["error"]

    # private skill 非管理员
    metadata = {"private.skill": {"visibility": "private"}}
    result = _check_and_execute("private.skill", {}, {}, metadata, {}, ctx)
    assert result["success"] == False
    assert "无权限" in result["error"]

    print("✅ test_check_and_execute passed")


if __name__ == "__main__":
    test_tool_schema()
    test_gateway_result()
    test_build_gateway_system_prompt()
    test_format_tool_result()
    test_check_and_execute()
    print("\n🎉 All tests passed!")
