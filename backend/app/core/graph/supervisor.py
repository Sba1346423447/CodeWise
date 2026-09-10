"""Supervisor 编排节点：集中持有主图路由决策（多 Agent 改造任务 1.5）。

设计要点（设计文档 §5 + 方案红线）：
- **确定性路由，非 LLM**：全部判定为纯 Python 规则（复用 edges.py 既有
  route 判定函数，逻辑零改动），禁止 LLM 自由输出节点名的"软路由"
- **Command 动态路由**：supervisor_node 返回 Command(goto=..., update=...)，
  取代主图 4 组静态条件边（route_after_coder / reviewer / tester / reflect
  的 add_conditional_edges）——路由决策单点持有、显式受控
- **stage 消歧**：静态边时代"哪条边触发"天然携带来源上下文；集中路由后
  由 state.stage（supervisor 派发时写入）记录来源，supervisor 凭它选择
  等价判定分支，决策表与旧静态边逐条映射，迁移等价
- 全局反思计数（reflection_count）与最优快照（best_code）由主图 state
  单点持有（1.2-1.4 迁移后已天然在编排层），supervisor 判定直接复用

决策表（stage = 上次派发的 goto 值（注册节点名）→ 本次 goto）：
| stage=""（初始） | coder（任务入口） |
| stage="coder"      | route_after_coder：问答/无产出→finalize_node；有代码→reviewer；其余→tester |
| stage="reviewer"   | route_after_reviewer：allow/批准→tester；block/拒绝→coder |
| stage="tester"     | route_after_tester：通过→finalize_node；失败→reflect_node |
| stage="reflect_node" | route_after_reflect：通过/超限→finalize_node；否则→coder（凭 critique 进 refine） |

stage 语义与 goto 值严格一致（均为 builder 注册节点名）——若两者不一致，
派发目标会落进防御分支导致路由失效。

收敛性：与旧静态图等价（判定函数逐条复用，reflection_count 上限 /
refine_no_progress 短路 / react_iterations 超限等防回环约束全部保留）。
"""

from langgraph.types import Command

from .edges import (
    route_after_coder,
    route_after_reflect,
    route_after_reviewer,
    route_after_tester,
)
from .state import OrchestrationState

# 旧路由函数返回的节点名 → 主图注册节点名 映射（历史返回值兼容复用）
_GOTO_MAP = {
    "code_review_node": "reviewer",
    "test_gen_node": "tester",
    "finalize_node": "finalize_node",
    "tester": "tester",
    "coder": "coder",
    "reflect_node": "reflect_node",
    "refine_node": "coder",
}


def supervisor_node(state: OrchestrationState) -> Command[OrchestrationState]:
    """编排决策：按 stage 选择等价判定分支，返回 Command 派发下一节点。

    update 同时写入 stage（本次派发目标），供下一轮决策消歧。
    """
    if not state.stage:
        # 初始派发：任务入口（coder 子图凭 critique 空自动走 react 生成）
        goto = "coder"
    elif state.stage == "coder":
        goto = _GOTO_MAP[route_after_coder(state)]
    elif state.stage == "reviewer":
        goto = _GOTO_MAP[route_after_reviewer(state)]
    elif state.stage == "tester":
        goto = _GOTO_MAP[route_after_tester(state)]
    elif state.stage == "reflect_node":
        goto = _GOTO_MAP[route_after_reflect(state)]
    else:
        # 防御：未知 stage（历史 checkpoint 兼容）直接收尾，不进入循环
        goto = "finalize_node"

    return Command(goto=goto, update={"stage": goto})
