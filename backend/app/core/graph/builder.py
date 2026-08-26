"""图构建器：组装 StateGraph，注册节点与边，编译为可执行的 Agent 图。

依赖：langgraph.graph（StateGraph / START / END）、langgraph.checkpoint.memory
（InMemorySaver）；节点与边定义见同目录 nodes.py / edges.py。
新拓扑（生成 → 审查 → 测试 → 反思 → 优化 → 交付）：
- START → react_node：ReAct 生成起点
- react_node → review_node / code_review_node / test_gen_node：有工具调用先过工具
  安全审查，已产出代码先过代码安全审查（test_node 会真实执行），均无则收尾
- review_node → tool_node / confirm_node / react_node：审查放行执行 / 挂起确认 / 拦截重决策
- confirm_node → tool_node / react_node：人工批准执行 / 拒绝换方案
- code_review_node → test_gen_node / code_confirm_node / react_node：代码审查放行 / 挂起确认 / 拦截重生成
- code_confirm_node → test_gen_node / react_node：人工批准执行 / 拒绝换方案
- tool_node → react_node：闭合 ReAct 循环
- test_gen_node → test_node：生成测试后强制真实 pytest
- test_node → finalize_node / test_gen_node / reflect_node：测试通过直接交付 /
  测试自身崩溃回炉重生成（代码不动）/ 真实失败进入反思
- reflect_node → finalize_node / refine_node：通过或超限收尾，否则优化重写
- refine_node → code_review_node：代码已变，先过安全审查再重新生成测试验证
- finalize_node → END：保证有交付物后结束

checkpointer：confirm_node / code_confirm_node 的 interrupt 挂起依赖 checkpoint
保存线程状态，用户批准/拒绝后以 Command(resume=...) 恢复同一线程。单用户场景用
InMemorySaver（零外部依赖）；多用户持久化可平滑替换为 SqliteSaver / PostgresSaver。
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .edges import (
    route_after_code_confirm,
    route_after_code_review,
    route_after_confirm,
    route_after_react,
    route_after_refine,
    route_after_reflect,
    route_after_review,
    route_after_test,
)
from .nodes import (
    code_confirm_node,
    code_review_node,
    confirm_node,
    finalize_node,
    react_node,
    refine_node,
    reflect_node,
    review_node,
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
    graph.add_node("review_node", review_node)
    graph.add_node("confirm_node", confirm_node)
    graph.add_node("code_review_node", code_review_node)
    graph.add_node("code_confirm_node", code_confirm_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("test_gen_node", test_gen_node)
    graph.add_node("test_node", test_node)
    graph.add_node("reflect_node", reflect_node)
    graph.add_node("refine_node", refine_node)
    graph.add_node("finalize_node", finalize_node)

    # 入口：START → react_node（ReAct 生成起点）
    graph.add_edge(START, "react_node")

    # react_node 出口：条件路由（通用问答直接交付 / 有代码先过代码审查 /
    # 有工具先过工具审查 / 否则收尾）
    # 显式 path map：LangGraph 依据返回值映射到已注册节点
    graph.add_conditional_edges(
        "react_node",
        route_after_react,
        {
            "review_node": "review_node",
            "code_review_node": "code_review_node",
            "test_gen_node": "test_gen_node",
            "finalize_node": "finalize_node",
        },
    )

    # review_node 出口：按审查结论分发（放行执行 / 挂起人工确认 / 拦截回决策）
    graph.add_conditional_edges(
        "review_node",
        route_after_review,
        {
            "tool_node": "tool_node",
            "confirm_node": "confirm_node",
            "react_node": "react_node",
        },
    )

    # confirm_node 出口：按人工确认结果分发（批准执行 / 拒绝回决策）
    graph.add_conditional_edges(
        "confirm_node",
        route_after_confirm,
        {
            "tool_node": "tool_node",
            "react_node": "react_node",
        },
    )

    # code_review_node 出口：按代码审查结论分发（放行进测试 / 挂起确认 / 拦截重生成）
    graph.add_conditional_edges(
        "code_review_node",
        route_after_code_review,
        {
            "test_gen_node": "test_gen_node",
            "code_confirm_node": "code_confirm_node",
            "react_node": "react_node",
        },
    )

    # code_confirm_node 出口：按人工确认结果分发（批准进测试 / 拒绝回决策）
    graph.add_conditional_edges(
        "code_confirm_node",
        route_after_code_confirm,
        {
            "test_gen_node": "test_gen_node",
            "react_node": "react_node",
        },
    )

    # tool_node 执行完工具后回到 LLM 决策，闭合 ReAct 循环
    graph.add_edge("tool_node", "react_node")

    # 测试链路：生成测试 → 真实执行
    graph.add_edge("test_gen_node", "test_node")

    # 测试出口条件路由（Codex 式：通过即交付，坏测试修测试，真实失败才反思）：
    # - 通过 → finalize_node（跳过反思，省一次 LLM 调用）
    # - 测试自身崩溃且未超重生成上限 → test_gen_node（重生成测试，代码不动）
    # - 真实失败 → reflect_node（失败详情注入，反思基于客观事实）
    graph.add_conditional_edges(
        "test_node",
        route_after_test,
        {
            "finalize_node": "finalize_node",
            "test_gen_node": "test_gen_node",
            "reflect_node": "reflect_node",
        },
    )

    # reflect_node 出口：条件路由（通过/超限/失效 → 收尾，否则 → refine）
    graph.add_conditional_edges("reflect_node", route_after_reflect)

    # refine_node 出口：条件路由（产出新代码 → 安全审查闭环；未产出 → 直接收尾，
    # 代码未变时后续节点全为同输入重复调用，见 route_after_refine 注释）
    graph.add_conditional_edges(
        "refine_node",
        route_after_refine,
        {
            "code_review_node": "code_review_node",
            "finalize_node": "finalize_node",
        },
    )

    # 最终交付：保证 final_code 非空后结束
    graph.add_edge("finalize_node", END)

    # checkpointer：支撑 confirm_node / code_confirm_node 的 interrupt 挂起与
    # Command(resume) 恢复
    return graph.compile(checkpointer=InMemorySaver())


# 模块级单例：全应用共享编译后的 Agent 图
agent_graph = build_agent_graph()
