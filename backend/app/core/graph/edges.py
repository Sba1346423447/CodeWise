"""条件边判定逻辑：根据工具调用 / 代码产出 / 测试结果 / 反思轮次给出路由结论。

依赖：langgraph.graph（END 常量）、pyyaml（读取 settings.yaml 反思轮次上限）。
多 Agent 拓扑（任务 1.5：Supervisor 集中编排）：主图无静态条件边，
各判定函数由 supervisor.py 按 stage 复用，结论经 _GOTO_MAP 映射为
Command(goto=...) 派发；子图内部（coder/tester/reviewer）仍用
add_conditional_edges 消费同批判定函数。

判定函数归属：
- route_after_react / route_after_review / route_after_confirm：
  Coder 子图内部（react ⇄ review/tool ReAct 循环）
- route_after_test：Tester 子图内部（坏测试回炉）+ supervisor（tester 出口）
- route_after_code_review：Reviewer 子图内部（confirm 链路）
- route_after_coder / route_after_reviewer / route_after_tester /
  route_after_reflect：supervisor 决策（主图各阶段出口）
"""

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .state import AgentState, OrchestrationState

# 项目根：edges -> graph -> core -> app -> backend -> 根
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _load_max_reflection_rounds() -> int:
    """读取 settings.yaml 的 agent.max_reflection_rounds；读取失败回退默认值 2。"""
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return int(raw.get("agent", {}).get("max_reflection_rounds", 2))
    except (OSError, TypeError, ValueError):
        return 2


# 模块级缓存，避免每次路由重复读盘
MAX_REFLECTION_ROUNDS = _load_max_reflection_rounds()

# ReAct 循环最大迭代次数（环境变量可覆盖）：交付压力下应极少触发，
# 作为最后兜底防止 react ↔ tool 死循环
MAX_REACT_ITERATIONS = int(os.getenv("MAX_REACT_ITERATIONS", "4"))

# 连续工具失败上限：达到后强制跳出 ReAct 循环进入测试链路（不再纠缠工具）
MAX_CONSECUTIVE_TOOL_FAILURES = 2


def _count_consecutive_tool_failures(messages: list[dict[str, Any]], limit: int) -> int:
    """从消息末尾向前统计连续工具失败次数，达到 limit 即返回 limit。

    工具结果以 JSON 字符串写入 tool 消息的 content 字段（含 success 字段），
    这里反向遍历解析，避免维护额外计数器。
    """
    count = 0
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            break
        try:
            payload = json.loads(msg.get("content") or "{}")
        except json.JSONDecodeError:
            break
        if payload.get("success") is False:
            count += 1
            if count >= limit:
                return count
        else:
            break
    return count


def route_after_react(state: AgentState) -> str:
    """react_node 之后路由（优先保证"产出代码先过安全审查"）：
    - 已产出 current_code → code_review_node（test_node 会真实执行代码，必须先审查）
    - 迭代超限 → test_gen_node（强制收尾，不再无限调工具）
    - 连续多次工具失败 → test_gen_node（LLM 反复重试同一无效操作时快速跳出）
    - 最近一条 assistant 消息带 tool_calls → review_node（先过安全审查链路）
    - 否则（无代码无工具）→ test_gen_node（进入收尾链路，finalize 兜底）
    """
    # 通用问答：react_node 已判定为纯文本回答，直接交付，跳过测试/反思/优化链路
    if state.is_answer_only:
        return "finalize_node"

    # 一旦产出代码，先过代码安全审查（最高优先级）
    if state.current_code and state.current_code.strip():
        return "code_review_node"

    # 迭代超限或连续工具失败：强制收尾
    if state.react_iterations >= MAX_REACT_ITERATIONS:
        return "test_gen_node"
    if _count_consecutive_tool_failures(state.messages, MAX_CONSECUTIVE_TOOL_FAILURES) >= MAX_CONSECUTIVE_TOOL_FAILURES:
        return "test_gen_node"

    if not state.messages:
        return "test_gen_node"

    last = state.messages[-1]
    if last.get("tool_calls"):
        return "review_node"
    if last.get("role") == "tool":
        return "react_node"
    return "test_gen_node"


def route_after_review(state: AgentState) -> str:
    """review_node 之后路由（按安全审查结论分发）：
    - allow（全部安全）→ tool_node 直接执行
    - confirm（存在可疑操作）→ confirm_node 挂起等待人工确认（interrupt）
    - block（拦截，拦截消息已回填 messages）→ react_node 让 LLM 重新决策
    """
    outcome = state.security_outcome
    if outcome == "allow":
        return "tool_node"
    if outcome == "confirm":
        return "confirm_node"
    return "react_node"


def route_after_confirm(state: AgentState) -> str:
    """confirm_node 之后路由（按人工确认结果分发）：
    - 批准 → tool_node 执行
    - 拒绝（拒绝消息已回填 messages）→ react_node 让 LLM 换方案
    """
    if state.security_confirmation:
        return "tool_node"
    return "react_node"


def route_after_code_review(state: OrchestrationState) -> str:
    """code_review_node 之后路由（按代码审查结论分发）：
    - allow（安全）→ test_gen_node 生成测试进入验证链路
    - confirm（网络外联等确认级）→ code_confirm_node 挂起等待人工确认（interrupt）
    - block（拦截级，拦截反馈已回填 messages 且代码已清空）→ react_node 重新生成

    多 Agent 改造（任务 1.4）：本函数由 Reviewer 子图内部复用——
    code_confirm_node 分支留在子图内，子图外分支经 END 由
    route_after_reviewer 等价接管
    """
    outcome = state.security_outcome
    if outcome == "allow":
        return "test_gen_node"
    if outcome == "confirm":
        return "code_confirm_node"
    return "react_node"


def route_after_reviewer(state: OrchestrationState) -> str:
    """reviewer 子图出口路由（多 Agent 改造任务 1.4）：

    子图 END 意味着审查链路已收敛（放行 / 拦截 / 人工确认已裁决），
    由本函数等价接管旧图 route_after_code_review 的子图外部分与
    route_after_code_confirm 的全部判定（code_confirm_node 出口在子图内
    改为无条件 END）：
    - allow（放行）→ tester 进入验证链路
    - confirm（批准后残留：code_confirm_node 批准分支只写
      security_confirmation 不改写 outcome；confirm 必经 code_confirm，
      子图 END 即已批准）→ tester
    - block（拦截 / 拒绝 / 异常兜底）→ coder 重新生成
    """
    if state.security_outcome in ("allow", "confirm"):
        return "tester"
    return "coder"


def route_after_test(state: OrchestrationState) -> str:
    """test_node 之后路由（Codex 式"验证是安全网，不是收费站"）：
    - 测试通过 → finalize_node 直接交付（跳过反思，省一次 LLM 调用）
    - 测试自身崩溃（test_broken）且未超重生成上限 → test_gen_node 重生成测试
      （代码不动，切断"坏测试 → 反思 → 改正确代码"空转）
    - 否则 → reflect_node（真实失败进入反思循环）

    多 Agent 改造（任务 1.3）：本函数由 Tester 子图内部复用——
    test_gen_node 回炉分支留在子图内，子图外分支经 END 由
    route_after_tester 等价接管
    """
    if state.tests_passed:
        return "finalize_node"
    if state.test_broken and state.test_regen_count < 1:
        return "test_gen_node"
    return "reflect_node"


def route_after_tester(state: OrchestrationState) -> str:
    """tester 子图出口路由（多 Agent 改造任务 1.3）：

    子图 END 意味着测试链路已收敛（通过 / 真实失败 / 坏测试回炉已耗尽），
    由本函数等价接管旧图 route_after_test 的子图外部分：
    - 测试通过 → finalize_node 直接交付
    - 否则（真实失败）→ reflect_node 进入反思循环
    """
    if state.tests_passed:
        return "finalize_node"
    return "reflect_node"


def route_after_reflect(state: OrchestrationState) -> str:
    """reflect_node 之后路由：
    - 测试通过 → finalize_node（交付最终代码）
    - 反思轮次已达上限（本轮为第 MAX 轮）→ finalize_node（交付当前实现 + 失败说明）
    - 否则 → refine_node（按修复意见重写后重新验证）
    """
    if state.tests_passed:
        return "finalize_node"
    # count 为已完成的 refine 轮次，本轮 reflect 尚未计入；+1 即当前已发生的 reflect 轮数，
    # 达到上限即收尾，确保总 reflect 次数恰为 MAX_REFLECTION_ROUNDS，不额外多跑一轮
    if state.reflection_count + 1 >= MAX_REFLECTION_ROUNDS:
        return "finalize_node"
    return "refine_node"


def route_after_coder(state: OrchestrationState) -> str:
    """coder 子图出口路由（多 Agent 改造任务 1.2）：

    子图 END 意味着 react / refine 的出口目标落在子图外，由本函数
    等价接管旧图 route_after_react / route_after_refine 的子图外部分：
    - 通用问答（is_answer_only）→ finalize_node 直接交付
    - refine 未产出新代码（refine_no_progress）→ finalize_node 收尾
      （判定必须先于 current_code：无产出时 current_code 保留原值非空）
    - 已产出 current_code → code_review_node（进测试执行前强制安全审查）
    - 其余（ReAct 超限 / 连续工具失败 / 无代码无工具）→ test_gen_node
      （进入收尾链路，finalize 兜底）
    """
    if state.is_answer_only:
        return "finalize_node"
    if state.refine_no_progress:
        return "finalize_node"
    if state.current_code and state.current_code.strip():
        return "code_review_node"
    return "test_gen_node"
