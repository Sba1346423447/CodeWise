"""Tester 子图：pytest 沙箱执行与退出码判定（多 Agent 改造任务 1.3）。

从主图迁入的节点（函数零改动，迁移等价）：
- test_gen_node：为当前代码生成 pytest 测试（简化模板 + 复用 + 崩溃重生成）
- test_node：真实执行 pytest，结果客观写入 tests_passed

子图内部拓扑：
- START → test_gen_node → test_node
- test_node 出口（route_tester_after_test）：测试自身崩溃（test_broken）且
  未超重生成上限 → 回 test_gen_node——**回炉循环封闭在子图内**，不再经
  主图路由往返（设计文档 4.2：test_broken → regen 是测试内部的自愈，
  对编排层而言应是一次原子验证）
- 其余（通过 / 真实失败）→ END 出子图，由主图 route_after_tester 分流
  （通过 → finalize 交付；失败 → reflect 反思）

state 语义：子图回传 test_code / tests_passed / test_results / test_broken /
test_regen_count / best_code 快照等全量，主图 LastValue 覆盖接收，一致。
无 interrupt 节点：测试链路无人工确认环节，挂起点仍在 Coder（工具确认）/
Reviewer（代码确认）子图内。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..edges import route_after_test
from ..nodes import test_gen_node, test_node
from ..state import AgentState


def route_tester_after_test(state: AgentState) -> str:
    """test_node 出口（子图版）：复用 route_after_test 判定。

    test_gen_node（坏测试回炉重生成）留在子图内；
    finalize_node / reflect_node 落在子图外 → 统一 END，去向由主图决定。
    """
    target = route_after_test(state)
    if target == "test_gen_node":
        return target
    return END


def build_tester_subgraph() -> CompiledStateGraph:
    """组装并编译 Tester 子图（schema 与旧 AgentState 一致，节点函数零改动）。"""
    graph = StateGraph(AgentState)

    graph.add_node("test_gen_node", test_gen_node)
    graph.add_node("test_node", test_node)

    graph.add_edge(START, "test_gen_node")
    graph.add_edge("test_gen_node", "test_node")

    # 坏测试回炉留子图内，通过/失败出子图（主图 route_after_tester 分流）
    graph.add_conditional_edges(
        "test_node",
        route_tester_after_test,
        {"test_gen_node": "test_gen_node", END: END},
    )

    return graph.compile()
