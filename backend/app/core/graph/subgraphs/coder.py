"""Coder 子图：ReAct 决策循环 + 按批判意见重写（多 Agent 改造任务 1.2）。

从旧单 Agent 图整体迁移的节点（函数与路由逻辑原样复用，迁移等价）：
- react_node：ReAct 决策（LLM 生成代码 / 调用工具）
- review_node：工具安全审查（规则过滤 + AI 风险分类）
- confirm_node：工具人工确认（interrupt 挂起）
- tool_node：工具执行
- refine_node：按 reflect 产出的批判意见重写代码

子图内部拓扑：
- START 条件入口：critique 非空 → refine_node（重写模式）；
  否则 → react_node（生成模式）。依据：critique 仅由 reflect_node 产出、
  refine_node 消费后清空，非空即"有待执行的重写任务"，判定确定性成立
- react_node ⇄ review_node → tool_node 闭合 ReAct 循环（全部子图内）
- react_node 出口：route_after_react 的返回值中 review_node / react_node
  留在子图内，其余（code_review_node / test_gen_node / finalize_node）
  落在子图外 → END 出子图，由主图路由接管
- refine_node 出口：route_after_refine 的两个目标均在子图外 → 直接 END

interrupt 挂起恢复：confirm_node 的 interrupt() 沿子图传播到主图执行流，
挂起与恢复（Command(resume=...)）由主图 checkpointer 持久化，恢复时从
子图内部断点继续，不重跑子图已完成节点（langgraph 1.2.7 实测验证）。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..edges import (
    route_after_confirm,
    route_after_react,
    route_after_review,
)
from ..nodes import (
    confirm_node,
    react_node,
    refine_node,
    review_node,
    tool_node,
)
from ..state import AgentState


def route_coder_entry(state: AgentState) -> str:
    """子图入口路由：有批判意见进重写，否则进 ReAct 生成。"""
    if (state.critique or "").strip():
        return "refine_node"
    return "react_node"


def route_coder_after_react(state: AgentState) -> str:
    """react_node 出口（子图版）：复用 route_after_react 判定。

    review_node（工具审查）与 react_node（防御分支）留在子图内；
    code_review_node / test_gen_node / finalize_node 落在子图外，
    统一 END 出子图，具体去向由主图 route_after_coder 决定。
    """
    target = route_after_react(state)
    if target in ("review_node", "react_node"):
        return target
    return END


def build_coder_subgraph() -> CompiledStateGraph:
    """组装并编译 Coder 子图（schema 与旧 AgentState 一致，节点函数零改动）。"""
    graph = StateGraph(AgentState)

    graph.add_node("react_node", react_node)
    graph.add_node("review_node", review_node)
    graph.add_node("confirm_node", confirm_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("refine_node", refine_node)

    # 条件入口：重写模式（critique 非空）→ refine；生成模式 → react
    graph.add_conditional_edges(
        START,
        route_coder_entry,
        {"react_node": "react_node", "refine_node": "refine_node"},
    )

    # react 出口：审查留子图内，其余出子图（主图路由接管）
    graph.add_conditional_edges(
        "react_node",
        route_coder_after_react,
        {"review_node": "review_node", "react_node": "react_node", END: END},
    )

    # review / confirm 出口目标全部在子图内，直接复用旧路由函数
    graph.add_conditional_edges(
        "review_node",
        route_after_review,
        {"tool_node": "tool_node", "confirm_node": "confirm_node", "react_node": "react_node"},
    )
    graph.add_conditional_edges(
        "confirm_node",
        route_after_confirm,
        {"tool_node": "tool_node", "react_node": "react_node"},
    )

    # 工具执行完回到 LLM 决策，闭合 ReAct 循环
    graph.add_edge("tool_node", "react_node")

    # refine 出口：code_review_node / finalize_node 均在子图外，直接 END
    graph.add_edge("refine_node", END)

    return graph.compile()
