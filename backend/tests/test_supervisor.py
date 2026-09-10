"""supervisor_node 编排决策单测（多 Agent 任务 1.5）。

决策表与旧静态条件边逐条映射（迁移等价）：
stage="" → coder；stage 按来源选 route 判定；Command.goto/update 断言。
"""

import pytest

from app.core.graph.edges import MAX_REFLECTION_ROUNDS
from app.core.graph.state import OrchestrationState
from app.core.graph.supervisor import supervisor_node


def _decision(**kwargs) -> tuple[str, str]:
    """构造 state 执行 supervisor 决策，返回 (goto, 新 stage)。"""
    cmd = supervisor_node(OrchestrationState(**kwargs))
    return cmd.goto, cmd.update["stage"]


class TestSupervisorDecision:
    def test_初始_stage空_派发coder生成(self):
        assert _decision() == ("coder", "coder")

    def test_coder完成_通用问答_收尾交付(self):
        assert _decision(stage="coder", is_answer_only=True) == ("finalize_node", "finalize_node")

    def test_coder完成_无产出_收尾交付(self):
        assert _decision(stage="coder", refine_no_progress=True) == ("finalize_node", "finalize_node")

    def test_coder完成_有代码_进审查(self):
        assert _decision(stage="coder", current_code="def f(): pass\n") == ("reviewer", "reviewer")

    def test_coder完成_无代码无问答_进测试收尾链路(self):
        assert _decision(stage="coder") == ("tester", "tester")

    def test_reviewer完成_放行_进测试(self):
        assert _decision(stage="reviewer", security_outcome="allow") == ("tester", "tester")

    def test_reviewer完成_批准后残留confirm_进测试(self):
        assert _decision(stage="reviewer", security_outcome="confirm") == ("tester", "tester")

    def test_reviewer完成_拦截_回coder重写(self):
        assert _decision(stage="reviewer", security_outcome="block") == ("coder", "coder")

    def test_tester完成_通过_交付(self):
        assert _decision(stage="tester", tests_passed=True) == ("finalize_node", "finalize_node")

    def test_tester完成_真实失败_进反思(self):
        assert _decision(stage="tester", tests_passed=False) == ("reflect_node", "reflect_node")

    def test_reflect完成_超限_收尾交付(self):
        assert _decision(
            stage="reflect_node", reflection_count=MAX_REFLECTION_ROUNDS
        ) == ("finalize_node", "finalize_node")

    def test_reflect完成_未超限_回coder重写(self):
        assert _decision(stage="reflect_node", tests_passed=False) == ("coder", "coder")

    def test_未知stage_防御性收尾(self):
        # 历史 checkpoint 兼容：未知 stage 不进入循环
        assert _decision(stage="legacy") == ("finalize_node", "finalize_node")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
