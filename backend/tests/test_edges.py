"""图条件边路由逻辑测试：route_after_react / route_after_code_review 等全分支。"""

import json

import pytest

from app.core.graph.edges import (
    MAX_REACT_ITERATIONS,
    MAX_REFLECTION_ROUNDS,
    route_after_code_confirm,
    route_after_code_review,
    route_after_confirm,
    route_after_react,
    route_after_reflect,
    route_after_review,
    route_after_test,
)
from app.core.graph.state import AgentState


def _tool_message(success: bool) -> dict:
    """构造一条 tool 消息（content 为工具结果 JSON，与 nodes.tool_node 格式对齐）。"""
    return {"role": "tool", "content": json.dumps({"success": success})}


class TestRouteAfterReact:
    def test_产出代码_先走代码安全审查(self):
        # 代码主链路安全审查加入后：test_node 会真实执行代码，必须先过 code_review_node
        state = AgentState(current_code="def f(): return 1\n")
        assert route_after_react(state) == "code_review_node"

    def test_迭代超限_强制收尾(self):
        state = AgentState(react_iterations=MAX_REACT_ITERATIONS)
        assert route_after_react(state) == "test_gen_node"

    def test_连续工具失败_跳出循环(self):
        state = AgentState(messages=[_tool_message(False), _tool_message(False)])
        assert route_after_react(state) == "test_gen_node"

    def test_有工具调用_先走安全审查(self):
        state = AgentState(
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "code_executor", "arguments": "{}"}}
                    ],
                }
            ]
        )
        # 安全审查链路加入后：工具调用先经 review_node（规则过滤 + AI 风险分类）
        assert route_after_react(state) == "review_node"

    def test_工具结果回填_回到react(self):
        state = AgentState(messages=[_tool_message(True)])
        assert route_after_react(state) == "react_node"

    def test_无消息_进入收尾链路(self):
        state = AgentState()
        assert route_after_react(state) == "test_gen_node"


class TestRouteAfterReview:
    """安全审查节点路由：allow 放行 / confirm 挂起确认 / block 回 react 重决策"""

    def test_审查放行_执行工具(self):
        state = AgentState(security_outcome="allow")
        assert route_after_review(state) == "tool_node"

    def test_需确认_挂起人工确认(self):
        state = AgentState(security_outcome="confirm")
        assert route_after_review(state) == "confirm_node"

    def test_拦截_回react重新决策(self):
        state = AgentState(security_outcome="block")
        assert route_after_review(state) == "react_node"

    def test_结论缺失_回react兜底(self):
        state = AgentState()
        assert route_after_review(state) == "react_node"


class TestRouteAfterConfirm:
    """人工确认节点路由：批准执行 / 拒绝回 react 换方案"""

    def test_用户批准_执行工具(self):
        state = AgentState(security_confirmation=True)
        assert route_after_confirm(state) == "tool_node"

    def test_用户拒绝_回react换方案(self):
        state = AgentState(security_confirmation=False)
        assert route_after_confirm(state) == "react_node"


class TestRouteAfterCodeReview:
    """代码安全审查节点路由：allow 进测试 / confirm 挂起确认 / block 回 react 重生成"""

    def test_审查放行_进入测试链路(self):
        state = AgentState(security_outcome="allow")
        assert route_after_code_review(state) == "test_gen_node"

    def test_需确认_挂起人工确认(self):
        state = AgentState(security_outcome="confirm")
        assert route_after_code_review(state) == "code_confirm_node"

    def test_拦截_回react重新生成(self):
        state = AgentState(security_outcome="block")
        assert route_after_code_review(state) == "react_node"

    def test_结论缺失_回react兜底(self):
        state = AgentState()
        assert route_after_code_review(state) == "react_node"


class TestRouteAfterCodeConfirm:
    """代码人工确认节点路由：批准进测试 / 拒绝回 react 换方案"""

    def test_用户批准_进入测试链路(self):
        state = AgentState(security_confirmation=True)
        assert route_after_code_confirm(state) == "test_gen_node"

    def test_用户拒绝_回react换方案(self):
        state = AgentState(security_confirmation=False)
        assert route_after_code_confirm(state) == "react_node"


class TestRouteAfterTest:
    """测试出口路由（Codex 式：通过即交付 / 坏测试修测试 / 真实失败才反思）"""

    def test_测试通过_跳过反思直接交付(self):
        state = AgentState(tests_passed=True)
        assert route_after_test(state) == "finalize_node"

    def test_测试崩溃且未超上限_重生成测试(self):
        state = AgentState(test_broken=True, test_regen_count=0)
        assert route_after_test(state) == "test_gen_node"

    def test_测试崩溃但已重生成过_进反思兜底(self):
        state = AgentState(test_broken=True, test_regen_count=1)
        assert route_after_test(state) == "reflect_node"

    def test_真实失败_进入反思(self):
        state = AgentState(tests_passed=False)
        assert route_after_test(state) == "reflect_node"


class TestRouteAfterReflect:
    def test_测试通过_直接交付(self):
        state = AgentState(tests_passed=True)
        assert route_after_reflect(state) == "finalize_node"

    def test_反思轮次超限_收尾(self):
        state = AgentState(reflection_count=MAX_REFLECTION_ROUNDS)
        assert route_after_reflect(state) == "finalize_node"

    def test_反思预算将尽且输出兜底_跳过无效refine(self):
        state = AgentState(
            reflection_count=MAX_REFLECTION_ROUNDS - 1,
            critique="未通过：当前实现未通过测试验证",
        )
        assert route_after_reflect(state) == "finalize_node"

    def test_未通过且有预算_进入优化(self):
        state = AgentState(tests_passed=False, reflection_count=0)
        assert route_after_reflect(state) == "refine_node"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
