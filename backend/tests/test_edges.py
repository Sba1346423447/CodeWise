"""图条件边路由逻辑测试：route_after_react / route_after_reflect 全分支。"""

import json

import pytest

from app.core.graph.edges import (
    MAX_REACT_ITERATIONS,
    MAX_REFLECTION_ROUNDS,
    route_after_react,
    route_after_reflect,
)
from app.core.graph.state import AgentState


def _tool_message(success: bool) -> dict:
    """构造一条 tool 消息（content 为工具结果 JSON，与 nodes.tool_node 格式对齐）。"""
    return {"role": "tool", "content": json.dumps({"success": success})}


class TestRouteAfterReact:
    def test_产出代码_进入测试链路(self):
        state = AgentState(current_code="def f(): return 1\n")
        assert route_after_react(state) == "test_gen_node"

    def test_迭代超限_强制收尾(self):
        state = AgentState(react_iterations=MAX_REACT_ITERATIONS)
        assert route_after_react(state) == "test_gen_node"

    def test_连续工具失败_跳出循环(self):
        state = AgentState(messages=[_tool_message(False), _tool_message(False)])
        assert route_after_react(state) == "test_gen_node"

    def test_有工具调用_走工具节点(self):
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
        assert route_after_react(state) == "tool_node"

    def test_工具结果回填_回到react(self):
        state = AgentState(messages=[_tool_message(True)])
        assert route_after_react(state) == "react_node"

    def test_无消息_进入收尾链路(self):
        state = AgentState()
        assert route_after_react(state) == "test_gen_node"


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
