"""Reviewer 子图：代码产物三层审查 + Human Gate（多 Agent 改造任务 1.4）。

从主图迁入的节点（函数零改动，迁移等价）：
- code_review_node：代码产物安全审查（L1 拦截级 / L1 确认级 / L3 AI 风险分类）
- code_confirm_node：代码人工确认（第四层 Human Gate，interrupt 挂起）

子图内部拓扑：
- START → code_review_node
- code_review_node 出口（route_reviewer_after_review，复用
  route_after_code_review 判定）：confirm（确认级）→ code_confirm_node
  挂起等人工确认；allow / block → END 出子图
- code_confirm_node 出口：无条件 END（批准/拒绝均出子图，去向由主图
  route_after_reviewer 按审查结论分流）

interrupt 挂起恢复：code_confirm_node 的 interrupt() 沿子图传播到主图
执行流，挂起与恢复（Command(resume=...)）由主图 checkpointer 持久化，
恢复时从子图内断点继续（机制与 Coder 子图内 confirm_node 一致，
langgraph 1.2.7 实测验证）。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..edges import route_after_code_review
from ..nodes import code_confirm_node, code_review_node
from ..state import AgentState


def route_reviewer_after_review(state: AgentState) -> str:
    """code_review_node 出口（子图版）：复用 route_after_code_review 判定。

    code_confirm_node（人工确认）留在子图内；
    allow（放行进测试）/ block（拦截回炉）落在子图外 → 统一 END，
    具体去向由主图 route_after_reviewer 决定。
    """
    target = route_after_code_review(state)
    if target == "code_confirm_node":
        return target
    return END


def build_reviewer_subgraph() -> CompiledStateGraph:
    """组装并编译 Reviewer 子图（schema 与旧 AgentState 一致，节点函数零改动）。"""
    graph = StateGraph(AgentState)

    graph.add_node("code_review_node", code_review_node)
    graph.add_node("code_confirm_node", code_confirm_node)

    graph.add_edge(START, "code_review_node")

    # 确认级留子图内挂起，放行/拦截出子图（主图路由接管）
    graph.add_conditional_edges(
        "code_review_node",
        route_reviewer_after_review,
        {"code_confirm_node": "code_confirm_node", END: END},
    )

    # 人工确认后无论批准/拒绝均出子图（批准→tester / 拒绝→coder）
    graph.add_edge("code_confirm_node", END)

    return graph.compile()
