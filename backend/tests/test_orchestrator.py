"""编排器交付结果组装测试：_build_result 对 None / dict / Pydantic 各形态兜底。"""

import pytest

from app.core.graph.state import AgentState
from app.core.orchestrator import AgentOrchestrator


def _expect_keys(result: dict) -> None:
    """交付结果必须包含的核心字段（与前端 AgentResult 契约对齐）。

    注：thinking 由 orchestrator.arun 在 _build_result 之后统一注入，
    故此处不校验（仅 empty 分支自带空列表）。
    """
    assert "task_desc" in result
    assert "final_code" in result
    assert "final_message" in result
    assert "messages" in result
    assert "reflection_count" in result
    assert "tests_passed" in result


class TestBuildResult:
    def test_无状态_返回空结果(self):
        result = AgentOrchestrator._build_result(None)
        _expect_keys(result)
        assert result["final_code"] == ""
        assert result["tests_passed"] is False
        assert result["thinking"] == []

    def test_langgraph_end包裹_剥壳(self):
        # LangGraph 1.x values 模式有时 yield {'__end__': state_dict}
        wrapped = {"__end__": {"task_desc": "t", "final_code": "code", "tests_passed": True}}
        result = AgentOrchestrator._build_result(wrapped)
        _expect_keys(result)
        assert result["final_code"] == "code"
        assert result["tests_passed"] is True

    def test_dict_直接读取(self):
        state = {
            "task_desc": "写一个LRU",
            "current_code": "current",
            "final_message": "完成",
            "reflection_count": 2,
            "tests_passed": True,
        }
        result = AgentOrchestrator._build_result(state)
        _expect_keys(result)
        assert result["final_code"] == "current"  # final_code 缺失时兜底取 current_code
        assert result["reflection_count"] == 2

    def test_pydantic实例_属性访问(self):
        state = AgentState(
            task_desc="t",
            final_code="final",
            current_code="current",
            final_message="m",
            reflection_count=1,
            tests_passed=False,
        )
        result = AgentOrchestrator._build_result(state)
        _expect_keys(result)
        assert result["final_code"] == "final"  # final_code 优先
        assert result["tests_passed"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
