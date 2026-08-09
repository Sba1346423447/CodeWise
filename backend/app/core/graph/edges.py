"""条件边逻辑：根据工具调用 / 代码产出 / 测试结果 / 反思轮次路由节点流转。

依赖：langgraph.graph（END 常量）、pyyaml（读取 settings.yaml 反思轮次上限）。
新拓扑（生成 → 测试 → 反思 → 优化 → 交付）：
- react_node → tool_node（有工具调用）或 test_gen_node（已产出代码）
- tool_node → react_node（闭合 ReAct 循环）
- test_gen_node → test_node
- test_node → reflect_node（无论通过与否都进入反思收尾）
- reflect_node → finalize_node（测试通过 / 反思轮次超限 / 反思失效）或 refine_node
- refine_node → test_gen_node（代码已变，重新生成测试后再验证）
- finalize_node → END
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import yaml
from langgraph.graph import END

from .state import AgentState

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

# 反思兜底文本前缀：与 nodes.py 的 reflect_node 兜底保持一致，用于判断反思是否产出有效意见
_FALLBACK_CRITIQUE_PREFIX = "未通过：当前实现未通过"


def _count_consecutive_tool_failures(messages: List[Dict[str, Any]], limit: int) -> int:
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
    """react_node 之后路由（优先保证"产出代码即进入测试链路"）：
    - 已产出 current_code → test_gen_node（生成测试后走强制测试）
    - 迭代超限 → test_gen_node（强制收尾，不再无限调工具）
    - 连续多次工具失败 → test_gen_node（LLM 反复重试同一无效操作时快速跳出）
    - 最近一条 assistant 消息带 tool_calls → tool_node（执行工具）
    - 否则（无代码无工具）→ test_gen_node（进入收尾链路，finalize 兜底）
    """
    # 通用问答：react_node 已判定为纯文本回答，直接交付，跳过测试/反思/优化链路
    if state.is_answer_only:
        return "finalize_node"

    # 一旦产出代码，立即进入测试链路（最高优先级）
    if state.current_code and state.current_code.strip():
        return "test_gen_node"

    # 迭代超限或连续工具失败：强制收尾
    if state.react_iterations >= MAX_REACT_ITERATIONS:
        return "test_gen_node"
    if _count_consecutive_tool_failures(state.messages, MAX_CONSECUTIVE_TOOL_FAILURES) >= MAX_CONSECUTIVE_TOOL_FAILURES:
        return "test_gen_node"

    if not state.messages:
        return "test_gen_node"

    last = state.messages[-1]
    if last.get("tool_calls"):
        return "tool_node"
    if last.get("role") == "tool":
        return "react_node"
    return "test_gen_node"


def route_after_reflect(state: AgentState) -> str:
    """reflect_node 之后路由：
    - 测试通过 → finalize_node（交付最终代码）
    - 反思轮次超限 → finalize_node（交付当前实现 + 失败说明）
    - 反思输出兜底文本且反思预算将尽 → finalize_node（避免无效 refine 空转）
    - 否则 → refine_node（按修复意见重写后重新验证）
    """
    if state.tests_passed:
        return "finalize_node"
    if state.reflection_count >= MAX_REFLECTION_ROUNDS:
        return "finalize_node"
    # 反思 LLM 输出兜底文本 + 反思预算将耗尽：跳过 refine，直接收尾
    if (state.critique.startswith(_FALLBACK_CRITIQUE_PREFIX)
            and state.reflection_count + 1 >= MAX_REFLECTION_ROUNDS):
        return "finalize_node"
    return "refine_node"
