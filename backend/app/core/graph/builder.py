"""图构建器：组装 StateGraph，注册节点与边，编译为可执行的 Agent 图。

依赖：langgraph.graph（StateGraph / START / END）；节点与边定义见同目录 nodes.py / edges.py。
新拓扑（生成 → 测试 → 反思 → 优化 → 交付）：
- START → react_node：ReAct 生成起点
- react_node → tool_node / test_gen_node：有工具调用走工具，有代码进入测试链路
- tool_node → react_node：闭合 ReAct 循环
- test_gen_node → test_node：生成测试后强制真实 pytest
- test_node → reflect_node：无论结果如何都进入反思（失败详情注入）
- reflect_node → finalize_node / refine_node：通过或超限收尾，否则优化重写
- refine_node → test_gen_node：代码已变，重新生成测试再验证
- finalize_node → END：保证有交付物后结束
"""

from langgraph.graph import END, START, StateGraph

from .edges import route_after_react, route_after_reflect
from .nodes import (
    finalize_node,
    react_node,
    refine_node,
    reflect_node,
    test_gen_node,
    test_node,
    tool_node,
)
from .state import AgentState


def build_agent_graph():
    """组装并编译 Agent 图，返回可执行图（LangGraph CompiledGraph）。"""
    graph = StateGraph(AgentState)

    # 注册节点
    graph.add_node("react_node", react_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("test_gen_node", test_gen_node)
    graph.add_node("test_node", test_node)
    graph.add_node("reflect_node", reflect_node)
    graph.add_node("refine_node", refine_node)
    graph.add_node("finalize_node", finalize_node)

    # 入口：START → react_node（ReAct 生成起点）
    graph.add_edge(START, "react_node")

    # react_node 出口：条件路由（通用问答直接交付 / 有代码进测试 / 有工具调工具 / 否则收尾）
    # 显式 path map：LangGraph 依据返回值映射到已注册节点
    graph.add_conditional_edges(
        "react_node",
        route_after_react,
        {
            "tool_node": "tool_node",
            "test_gen_node": "test_gen_node",
            "finalize_node": "finalize_node",
        },
    )

    # tool_node 执行完工具后回到 LLM 决策，闭合 ReAct 循环
    graph.add_edge("tool_node", "react_node")

    # 测试链路：生成测试 → 真实执行
    graph.add_edge("test_gen_node", "test_node")

    # 无论测试结果如何都进入反思审查（失败详情注入，让反思基于客观事实）
    graph.add_edge("test_node", "reflect_node")

    # reflect_node 出口：条件路由（通过/超限/失效 → 收尾，否则 → refine）
    graph.add_conditional_edges("reflect_node", route_after_reflect)

    # refine_node 重写后重新生成测试再验证，形成"重写 → 测试 → 审查"自纠正闭环
    graph.add_edge("refine_node", "test_gen_node")

    # 最终交付：保证 final_code 非空后结束
    graph.add_edge("finalize_node", END)

    return graph.compile()


# 模块级单例：全应用共享编译后的 Agent 图
agent_graph = build_agent_graph()
