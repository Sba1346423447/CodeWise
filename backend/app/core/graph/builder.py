"""图构建器：组装多 Agent 主图（Supervisor 编排 + 三子图），编译为可执行 Agent 图。

多 Agent 改造（任务 1.2 + 1.3 + 1.4 + 1.5）：旧 11 节点扁平图重组为
「Supervisor 编排节点 + Coder / Reviewer / Tester 三子图 + 编排层节点」：
- Supervisor（supervisor.py）：集中路由决策——Command(goto=...) 动态派发，
  确定性规则（复用 edges.py 既有判定），取代 4 组静态条件边
- Coder 子图（subgraphs/coder.py，独立编译）：react / review / confirm /
  tool / refine——ReAct 决策循环与重写链路整体迁移，节点函数零改动
- Reviewer 子图（subgraphs/reviewer.py，独立编译）：code_review /
  code_confirm——代码产物三层审查 + Human Gate（interrupt 在子图内挂起，
  恢复后从子图断点继续）
- Tester 子图（subgraphs/tester.py，独立编译）：test_gen / test——
  pytest 沙箱执行与判定，坏测试回炉重生成循环封闭在子图内
- 编排层节点：reflect（反思）/ finalize（交付 + best_code 快照回退）

主图拓扑（Supervisor 集中编排）：
- START → supervisor：任务入口（stage 空 → 派发 coder 生成模式）
- coder / reviewer / tester / reflect_node 完成后 → 全部回到 supervisor
  （各阶段判定由 supervisor 按 stage 选择等价 route 函数，见 supervisor.py）
- supervisor → Command(goto=...)：coder / reviewer / tester / reflect_node /
  finalize_node（goto 目标均已注册）
- finalize_node → END：保证有交付物后结束

state 分层（OrchestrationState 继承 AgentState）：
- messages / reflections 主图用覆盖 reducer：子图（append reducer）内部
  累积全量后回传，主图覆盖接收，避免子图全量回传叠加重复
  （langgraph 1.2.7 实测：子图作为节点会回传子图最终 state 全量）
- stage：supervisor 派发目标记录，集中路由的来源消歧依据
- 其余字段覆盖语义（LastValue），子图注入/回传天然一致；
  reflection_count（全局反思计数）与 best_code（最优快照）由主图
  state 单点持有，supervisor / finalize 判定直接读写

checkpointer：Coder 子图 confirm_node 与 Reviewer 子图 code_confirm_node
的 interrupt 挂起依赖 checkpoint 保存线程状态；子图内 interrupt 沿子图
传播至主图执行流，挂起与恢复（Command(resume=...)）由主图 checkpointer
持久化，恢复时从子图内部断点继续、子图 END 后经静态边回 supervisor，
与 Command(goto) 动态路由兼容（resume 与 goto 作用于不同层级）。
- 默认 InMemorySaver（测试 / 无 DB 环境零依赖，重启丢状态）
- 生产持久化：main.py lifespan 按 settings.yaml agent.checkpointer=mysql
  创建 AIOMySQLSaver（见 checkpoint.py）并以 build_agent_graph(saver)
  重建单例——重启后同 thread_id 从断点继续（改造三 3.1）
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes import (
    finalize_node,
    reflect_node,
)
from .state import OrchestrationState
from .subgraphs.coder import build_coder_subgraph
from .subgraphs.reviewer import build_reviewer_subgraph
from .subgraphs.tester import build_tester_subgraph
from .supervisor import supervisor_node


def build_agent_graph(checkpointer=None):
    """组装并编译 Agent 主图，返回可执行图（LangGraph CompiledGraph）。

    checkpointer：None 时默认 InMemorySaver；生产持久化由 main.py lifespan
    传入 AIOMySQLSaver（见 checkpoint.py）后重建单例。
    """
    graph = StateGraph(OrchestrationState)

    # 注册节点：coder / reviewer / tester 为独立编译的子图，
    # supervisor 为编排决策节点，reflect / finalize 为编排层节点。
    # input_schema 显式指定 OrchestrationState：节点函数注解保持 AgentState
    # 不动（测试兼容），主图按 OrchestrationState（messages/reflections
    # 覆盖语义 + stage）注册 channel，避免子图全量回传时 append 叠加重复
    graph.add_node("supervisor", supervisor_node, input_schema=OrchestrationState)
    graph.add_node("coder", build_coder_subgraph())
    graph.add_node("reviewer", build_reviewer_subgraph())
    graph.add_node("tester", build_tester_subgraph())
    graph.add_node("reflect_node", reflect_node, input_schema=OrchestrationState)
    graph.add_node("finalize_node", finalize_node, input_schema=OrchestrationState)

    # 入口：START → supervisor（初始派发 coder 生成模式）
    graph.add_edge(START, "supervisor")

    # 各节点完成后统一回 supervisor 集中决策
    # （旧 4 组静态条件边的判定全部收拢进 supervisor_node，按 stage 分发）
    graph.add_edge("coder", "supervisor")
    graph.add_edge("reviewer", "supervisor")
    graph.add_edge("tester", "supervisor")
    graph.add_edge("reflect_node", "supervisor")

    # supervisor 出口由 Command(goto=...) 动态派发（无需静态出边）

    # 最终交付：保证 final_code 非空后结束
    graph.add_edge("finalize_node", END)

    # checkpointer：支撑 confirm_node / code_confirm_node 的 interrupt 挂起与
    # Command(resume) 恢复（含子图内部断点）；None 默认内存版（重启丢状态）
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# 模块级单例：全应用共享编译后的 Agent 图
agent_graph = build_agent_graph()
