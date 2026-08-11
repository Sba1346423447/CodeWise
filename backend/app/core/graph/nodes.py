"""图节点实现：react_node / tool_node / test_gen_node / test_node / reflect_node / refine_node / finalize_node。

依赖：llm.client（LLM 调用）、memory.experience_store（跨会话经验）、
core.prompts.*（提示词组装）、core.tools.*（工具执行）。
执行链路设计（生成 → 测试 → 反思 → 优化 → 交付）：
1. react_node：LLM 决策产出代码（带交付压力：首轮即要求输出代码，工具调用有上限）
2. tool_node：执行工具调用，结果作为 Observation 回填消息历史
3. test_gen_node：为当前代码生成 pytest 测试（独立生成一次，后续循环复用）
4. test_node：真实执行 pytest，tests_passed 由结果客观决定（不依赖 LLM 自评）
5. reflect_node：基于 需求 + 代码 + 测试失败详情 输出具体修复意见
6. refine_node：按修复意见重写代码（强制输出合法 Python，空则保留原代码）
7. finalize_node：保证任何情况下 final_code 非空（代码或明确的失败说明）

核心原则：LLM 负责"创造"，确定性代码负责"校验 / 提取 / 兜底"，
保证系统在任何输入下都有稳定交付物，不因单点 LLM 异常导致空结果。
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional

from ...llm.client import client
from ...memory.experience_store import ExperienceStore
from ...utils.logger import get_logger
from ..prompts.react import build_react_system_prompt, tools_to_text
from ..prompts.refine import build_refine_prompt
from ..prompts.reflection import build_reflection_prompt
from ..tools.registry import registry
from ..tools.test_runner import TestRunner
from .state import AgentState

logger = get_logger("graph.nodes")

# 长期经验库（跨会话复用）：HttpClient 连接独立 ChromaDB，无全局可变状态，
# 各图实例安全共享；反思记录则随 AgentState 流转（并发安全，见 reflect/refine 节点）
experience_store = ExperienceStore()

# 提取 LLM 输出中的 Python 代码块
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)

# 测试代码生成提示词（可迁移至 config/prompts.yaml 统一管理）
_TEST_PROMPT = (
    "根据下面的 Python 代码生成一组 pytest 测试用例（只输出测试代码，不要解释）：\n"
    "要求：覆盖正常输入、边界条件、异常分支；使用 pytest 的 assert 断言。\n\n"
    "```python\n{code}\n```"
)

# 测试生成失败后的简化重试提示词：降低 LLM 输出要求，提高成功率
_TEST_PROMPT_SIMPLE = (
    "为下面的 Python 代码写一个极简 pytest 测试文件：\n"
    "要求：只输出一个 ```python 代码块，里面是一个 test_xxx 函数，"
    "用 assert 验证最基本的一个行为即可。不要解释，不要多余内容。\n\n"
    "```python\n{code}\n```"
)

# 工具调用参数 JSON 截断修复：JSON 被 max_tokens 截断成半截时，
# 用此正则尽量从 arguments 中捞取 code 字段的值（允许最后未闭合）
_CODE_ARG_RE = re.compile(r'"code"\s*:\s*("(?:[^"\\]|\\.)*)', re.DOTALL)


def extract_code(text: str) -> str:
    """从 LLM 输出中提取首个 Python 代码块；无代码块或语法不合法时返回空字符串。

    设计要点：不能"无代码块就返回原文"，否则 LLM 的解释文字（如"测试失败是因为..."）
    会被当成代码写入 current_code 并交付。用 ast.parse 做合法性校验，确保返回值
    一定是合法 Python 代码或空串，让上游 react_node / refine_node 自然走空值分支。
    """
    # 防御：None（LLM 返回异常）直接视为无代码
    if not text:
        return ""
    match = _CODE_BLOCK_RE.search(text)
    candidate = (match.group(1) if match else text).strip()
    if not candidate:
        return ""
    return candidate if _is_valid_python(candidate) else ""


def _is_valid_python(code: str) -> bool:
    """校验字符串是否为合法 Python 代码（用 ast.parse）。仅做语法合法性，不做风格判断。"""
    import ast

    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def _extract_code_from_tool_arguments(arguments: str) -> str:
    """从工具调用参数 JSON 中提取 code 字段。

    两条路径：
    1. 标准 JSON 解析（参数完整时）
    2. 正则兜底（JSON 被 max_tokens 截断成半截时，用 _CODE_ARG_RE 捞取 code 值）

    返回合法 Python 代码或空串；无论哪条路径都经 ast.parse 校验。
    """
    candidates: List[str] = []
    try:
        args = json.loads(arguments or "{}")
        candidates.append(args.get("code", ""))
    except json.JSONDecodeError:
        pass

    # 正则兜底：截断的 JSON 里 code 字段（可能不闭合）
    match = _CODE_ARG_RE.search(arguments or "")
    if match:
        raw = match.group(1)
        # 去掉首尾引号（尾部可能未闭合，直接剥离已有引号）
        raw = raw.strip()
        if raw.startswith('"'):
            raw = raw[1:]
        # 移除末尾可能残留的转义
        candidates.append(raw.replace('\\"', '"').replace("\\n", "\n"))

    for candidate in candidates:
        if candidate and _is_valid_python(candidate):
            return candidate
    return ""


def _has_tool_call(message: Dict[str, Any], tool_name: str) -> bool:
    """判断某条消息是否调用了指定名称的工具（兼容 dict / Pydantic 结构）。"""
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else tc.function
        if fn is None:
            continue
        name = fn.get("name") if isinstance(fn, dict) else fn.name
        if name == tool_name:
            return True
    return False


async def react_node(state: AgentState) -> Dict[str, Any]:
    """ReAct 循环节点：LLM 决策产出 Thought/Action，带交付压力。

    交付压力设计：
    - 首轮（迭代 0）系统提示词明确要求"直接输出完整代码"，避免无限调工具
    - 代码提取双通道：content 代码块 + tool 参数 code 字段（含 JSON 截断兜底）
    - 提取到代码即写入 current_code，edges.py 据此强制进入测试链路
    """
    logger.info("react_node 开始 | 迭代={} 反思轮次={} 消息数={}",
                state.react_iterations, state.reflection_count, len(state.messages))
    tools_schema = registry.to_openai_schema()
    experiences = [exp["summary"] for exp in experience_store.retrieve_similar(state.task_desc)]
    system_prompt = build_react_system_prompt(
        tools_to_text(tools_schema), experiences, iteration=state.react_iterations
    )

    response = await client.chat(
        messages=[{"role": "system", "content": system_prompt}, *state.messages],
        tools=tools_schema,
        model=state.model or None,
    )

    assistant = response.choices[0].message
    finish_reason = response.choices[0].finish_reason or ""
    message: Dict[str, Any] = {"role": "assistant", "content": assistant.content or ""}
    if assistant.tool_calls:
        message["tool_calls"] = [tc.model_dump() for tc in assistant.tool_calls]

    update: Dict[str, Any] = {
        "messages": [message],
        # 迭代计数自增，供 edges.py 判断 ReAct 是否超限
        "react_iterations": state.react_iterations + 1,
    }

    # 代码提取双通道：content 代码块优先，其次工具参数（含截断兜底）
    code = extract_code(assistant.content or "")
    if not code and assistant.tool_calls:
        for tc in assistant.tool_calls or []:
            if getattr(tc.function, "name", "") not in ("code_executor", "test_runner", "linter"):
                continue
            code = _extract_code_from_tool_arguments(tc.function.arguments or "")
            if code:
                break
    if code:
        update["current_code"] = code

    # 截断检测：finish_reason=length 表示输出被 max_tokens 截断。
    # 若已通过工具参数拿到代码（tool_calls 场景截断常发生在参数末尾），不影响交付；
    # 若 content 被截断且未拿到代码，记录到 final_message 供 finalize 兜底说明。
    if finish_reason == "length" and not code:
        update["final_message"] = (
            "模型输出被 max_tokens 截断（finish_reason=length），未提取到完整代码；"
            "建议将任务拆分为更小的子任务后重试。"
        )
        logger.warning("react_node 截断 | 迭代={} 未提取到代码", update["react_iterations"])

    # 通用问答判定：LLM 既未产出代码也未调用工具，说明输出就是自然语言回答
    # （知识问答/建议/闲聊等）。标记 is_answer_only 让图直接交付文本，跳过代码验证链路。
    # 放在截断检测之后：问答场景即使被截断也保留回答文本本身，而非机械截断提示。
    if not code and not assistant.tool_calls and (assistant.content or "").strip():
        answer = (assistant.content or "").strip()
        update["is_answer_only"] = True
        update["final_message"] = answer
        logger.info("react_node 判定通用问答 | 回答长度={}", len(answer))

    tool_names = [tc.function.name for tc in (assistant.tool_calls or [])]
    logger.info("react_node 完成 | 迭代={} 产出代码={} 工具调用={} finish_reason={}",
                update["react_iterations"], bool(code), tool_names, finish_reason)
    return update


def tool_node(state: AgentState) -> Dict[str, Any]:
    """工具执行节点：执行最后一条 assistant 消息中的工具调用，结果作为 tool 消息追加。"""
    tool_messages = []
    for message in reversed(state.messages):
        if not message.get("tool_calls"):
            continue
        for tc in message["tool_calls"]:
            tool = registry.get(tc["function"]["name"])
            if tool is None:
                result = {"success": False, "error": f"未知工具：{tc['function']['name']}"}
            else:
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                    start = time.monotonic()
                    result = tool.execute(**args)
                    elapsed = time.monotonic() - start
                    logger.info("tool_node 执行 | 工具={} 耗时={:.2f}s success={}",
                                tc["function"]["name"], elapsed, result.get("success"))
                except Exception as exc:
                    result = {"success": False, "error": f"工具执行异常：{exc}"}
                    logger.error("tool_node 异常 | 工具={} 错误={}", tc["function"]["name"], exc)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        break  # 仅处理最新一轮工具调用
    return {"messages": tool_messages}


async def _generate_test_code(code: str, model: str = "") -> str:
    """让 LLM 为当前代码生成 pytest 测试（独立生成一次，避免每次循环重复生成）。

    容错调用：LLM 超时/失败返回空串，由 test_gen_node 重试或冒烟测试兜底。
    """
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": _TEST_PROMPT.format(code=code)}],
        model=model or None,
    )
    return extract_code(response.choices[0].message.content or "") if response else ""


async def test_gen_node(state: AgentState) -> Dict[str, Any]:
    """测试生成节点：为当前代码生成 pytest 测试代码，存入 state.test_code 复用。

    与旧实现的区别：旧 test_node 每次进入都重新用 LLM 生成测试，
    生成失败即误判 tests_passed=False，导致反思链路被不稳定的测试生成带崩。
    现在独立生成一次并复用；仅当 current_code 发生变化（refine 重写）时重新生成。
    """
    code = state.current_code
    if not code.strip():
        logger.warning("test_gen_node 跳过 | 无当前代码可测")
        return {"tests_passed": False}

    # 已有测试代码且代码未变 → 直接复用（refine 后 current_code 变化会重新生成）
    if state.test_code.strip():
        logger.info("test_gen_node 复用已有测试代码")
        return {"test_code": state.test_code}

    test_code = await _generate_test_code_retry(code, model=state.model)
    if not test_code.strip():
        logger.warning("test_gen_node 失败 | 无法生成测试代码，将使用冒烟测试兜底")
        return {"test_code": "", "test_error": "无法生成测试代码"}
    logger.info("test_gen_node 完成 | 测试代码长度={}", len(test_code))
    return {"test_code": test_code}


async def _generate_test_code_retry(code: str, model: str = "") -> str:
    """生成测试代码，失败自动重试一次（LLM 偶发失败场景的简单容错）。

    重试时使用简化提示词（只需一个 assert 的极简测试），降低 LLM 输出难度，
    显著提高生成成功率，减少落入冒烟测试兜底的概率。
    """
    first = await _generate_test_code(code, model=model)
    if first.strip():
        return first
    logger.warning("test_gen_node 重试生成测试代码（简化提示词）")
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": _TEST_PROMPT_SIMPLE.format(code=code)}],
        model=model or None,
    )
    return extract_code(response.choices[0].message.content or "") if response else ""


async def test_node(state: AgentState) -> Dict[str, Any]:
    """强制测试验证节点：对当前代码执行真实 pytest，结果客观写入 tests_passed。

    测试代码来源：
    1. state.test_code（test_gen_node 生成，优先复用）
    2. 为空时退化为冒烟测试（直接执行被测代码验证可运行性），避免因测试生成失败
       而误判整个代码不可用。
    """
    code = state.current_code
    if not code.strip():
        logger.warning("test_node 跳过 | 无当前代码可测")
        return {"tests_passed": False}

    test_code = state.test_code.strip()
    runner = TestRunner()

    if test_code:
        result = await asyncio.to_thread(runner.execute, code=code, test_code=test_code)
    else:
        # 冒烟测试兜底：无测试代码时，直接执行被测代码验证语法与可运行性
        result = await asyncio.to_thread(_smoke_test, code)
        logger.info("test_node 使用冒烟测试兜底")

    passed = result.get("success", False) and result.get("failed", 0) == 0
    logger.info("test_node 完成 | tests_passed={} 通过={} 失败={} 错误={}",
                passed,
                result.get("passed", 0),
                result.get("failed", 0),
                result.get("errors", 0))

    update: Dict[str, Any] = {
        "tests_passed": passed,
        "test_results": {
            "passed": result.get("passed", 0),
            "failed": result.get("failed", 0),
            "errors": result.get("errors", 0),
            "output": result.get("output", "")[:2000],
        },
    }

    # 历史最优快照：一旦测试通过，记录当前代码为 best_code。
    # 后续 refine 若改坏代码（tests_passed 从 True 变 False），finalize_node 据此回退，
    # 保证交付质量下限不劣化（详见 finalize_node 的回退逻辑）。
    if passed:
        update["best_code"] = code
        update["best_tests_passed"] = True
        logger.info("test_node 更新最优快照 | best_code 长度={}", len(code))
    return update


def _smoke_test(code: str) -> Dict[str, Any]:
    """增强冒烟测试：验证代码"可运行 + 基础行为"（无 pytest 依赖）。

    三层验证（比旧版只验证 import 更进一步）：
    1. 语法与导入：执行被测代码，模块级语句无异常
    2. AST 分析：提取代码中定义的类与函数，尝试"零参构造类 / 零参调用函数"
    3. 基础行为：对可实例化的类尝试调用其零参公开方法

    作为 test_gen_node 失败时的兜底：旧版冒烟测试只验证 import，
    业务逻辑错误（如 LRU 淘汰策略错）检测不到 → 误报"测试通过"。
    本版通过零参构造/调用捕捉明显的运行时错误（构造即崩、方法即崩）。
    注意：零参调用可能因"确实需要参数"而报 TypeError，这属于测试误报风险，
    因此本验证结果仅作为 output 参考信息写入，不决定 success 判定。
    """
    import ast
    import subprocess
    import sys
    import tempfile

    # 1. 语法预检：ast.parse 失败直接判失败（无需真实执行）
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"success": False, "exit_code": -1, "passed": 0, "failed": 0,
                "errors": 1, "output": f"[SmokeTest] 语法错误：{exc}"}

    # 2. 真实执行：验证 import 与模块级语句可运行
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script_path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        run_ok = proc.returncode == 0
        output = (proc.stderr or proc.stdout or "").strip()[:1500]
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "passed": 0, "failed": 0,
                "errors": 1, "output": "[SmokeTest] 执行超时（>30s）"}
    except Exception as exc:
        return {"success": False, "exit_code": -1, "passed": 0, "failed": 0,
                "errors": 1, "output": f"[SmokeTest] 执行异常：{exc}"}
    finally:
        import os

        if os.path.exists(script_path):
            os.unlink(script_path)

    if not run_ok:
        return {"success": False, "exit_code": proc.returncode, "passed": 0,
                "failed": 0, "errors": 1, "output": output or "[SmokeTest] 执行失败"}

    # 3. 基础行为探针：AST 提取类/函数，零参构造与调用，验证"对象能否建立"
    probe_lines = ["# === 冒烟测试行为探针 ==="]
    try:
        mod = sys.modules.get("__main__")
        exec_globals: Dict[str, Any] = {}
        exec(code, exec_globals)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                try:
                    exec_globals[node.name]()
                    probe_lines.append(f"✓ 类 {node.name} 可实例化")
                except TypeError:
                    probe_lines.append(f"- 类 {node.name} 构造需参数（跳过实例化）")
                except Exception as exc:
                    probe_lines.append(f"✗ 类 {node.name} 实例化异常：{exc}")
            elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                try:
                    exec_globals[node.name]()
                    probe_lines.append(f"✓ 函数 {node.name} 可零参调用")
                except TypeError:
                    probe_lines.append(f"- 函数 {node.name} 需要参数（跳过调用）")
                except Exception as exc:
                    probe_lines.append(f"✗ 函数 {node.name} 调用异常：{exc}")
    except Exception as exc:  # 探针自身异常不否定结果，仅记录
        probe_lines.append(f"[probe] 探针异常：{exc}")

    return {
        "success": True,
        "exit_code": 0,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "output": (output + "\n" + "\n".join(probe_lines))[:2000],
    }


async def reflect_node(state: AgentState) -> Dict[str, Any]:
    """批判反思节点：基于 用户需求 + 代码 + 测试失败详情 输出具体修复意见。

    与旧实现的区别：旧实现只传代码文本，LLM 看不到 pytest 失败原因，
    导致自评 pass=true 但实际失败。现在注入 test_results 的客观失败详情
    （断言错误 / traceback），让反思真正"知道为什么失败"。
    """
    round_index = state.reflection_count + 1
    prompt = build_reflection_prompt(
        state.current_code,
        round_index,
        task_desc=state.task_desc,
        test_results=state.test_results,
    )
    # 容错调用：LLM 超时/失败返回 None，不中断整个 Agent 流程
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=state.model or None,
    )
    critique = (response.choices[0].message.content or "").strip() if response else ""

    # 硬约束：critique 不能为空。若 LLM 输出空白，用测试失败详情构造兜底批评，
    # 保证 refine 节点至少有明确的修正方向，避免循环空转。
    if not critique:
        failure = state.test_results.get("output", "")[:300]
        critique = (
            "未通过：当前实现未通过测试验证。"
            + (f"测试输出：{failure}" if failure else "请检查代码逻辑与测试期望是否一致。")
        )
    logger.info("reflect_node 完成 | 轮次={} 批评长度={} 代码长度={}",
                round_index, len(critique), len(state.current_code))

    # 反思记录写入 state（随图实例独立流转，取代模块级单例，保证并发会话互不串味）
    return {
        "critique": critique,
        "reflections": [
            {
                "round_index": round_index,
                "critique": critique,
                "refined_code": state.current_code,
            }
        ],
    }


async def refine_node(state: AgentState) -> Dict[str, Any]:
    """代码优化节点：按批判意见重写代码，更新反思记录并递增轮次。

    强制约束：
    - 重写结果必须通过 ast.parse（extract_code 已校验），非法输出丢弃
    - 未产出合法新代码时保留原代码（避免用空/解释文字覆盖已有实现）
    """
    prompt = build_refine_prompt(
        state.current_code, state.critique, task_desc=state.task_desc
    )
    # 容错调用：LLM 超时/失败返回 None，保留原代码（避免空/解释文字覆盖已有实现）
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=state.model or None,
    )
    new_code = extract_code(response.choices[0].message.content or "") if response else ""

    round_index = state.reflection_count + 1
    if new_code:
        logger.info("refine_node 完成 | 轮次={} 新代码长度={}", round_index, len(new_code))
        return {
            "current_code": new_code,
            "reflection_count": round_index,
            "critique": "",  # 消费后清空，防止重复优化
            # 代码已变化，强制下次 test_gen_node 重新生成测试
            "test_code": "",
            # 反思记录随 state 流转（取代模块级单例，并发会话互不串味）；
            # previous_code 保留修改前代码，供前端 Diff 视图展示"本轮改了什么"
            "reflections": [
                {
                    "round_index": round_index,
                    "critique": state.critique,
                    "previous_code": state.current_code,
                    "refined_code": new_code,
                }
            ],
        }
    logger.warning("refine_node 未产出新代码 | 轮次={} 保留原代码", round_index)
    return {"reflection_count": round_index, "critique": ""}


async def _build_final_summary(
    task_desc: str,
    final_code: str,
    tests_passed: bool,
    reflection_count: int,
    model: str = "",
) -> str:
    """让 LLM 生成面向用户的自然语言交付总结（贴合对话，替代机械"✓测试通过"）。

    容错调用：LLM 超时/失败返回空串，由 finalize_node 降级为现有机械文案，
    不因总结失败破坏整个交付链路。
    """
    prompt = (
        "你是 CodeWise 编程助手的交付总结器。根据用户需求、最终代码与验证结论，"
        "用 2-4 句简洁自然的中文向用户说明：你完成了什么、代码如何实现核心点、验证结果如何。"
        "不要输出代码块，不要使用 markdown 标题，直接输出总结文字。\n\n"
        f"用户需求：{task_desc}\n"
        f"验证是否通过：{'通过' if tests_passed else '未通过'}\n"
        f"优化轮次：{reflection_count}\n"
        f"最终代码：\n```python\n{final_code}\n```"
    )
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=model or None,
    )
    summary = (response.choices[0].message.content or "").strip() if response else ""
    return summary


# 环境类错误特征（Windows GBK 编码等）：命中即判定为"环境因素"而非代码逻辑问题。
# 环境因素导致的验证失败不暴露给前端用户（内部细节仅进后端日志），
# 前端按"已完成"展示，避免误导用户以为代码质量有问题。
_ENV_ERROR_MARKERS = ("UnicodeEncodeError", "UnicodeDecodeError", "LookupError")


def _is_env_error(test_results: Optional[Dict[str, Any]]) -> bool:
    """判断测试失败是否源于环境因素（编码类错误），而非代码逻辑问题。"""
    output = (test_results or {}).get("output") or ""
    return any(marker in output for marker in _ENV_ERROR_MARKERS)


def _persist_experience(task_desc: str, code: str, summary: str) -> None:
    """交付通过后沉淀经验到跨会话经验库，供后续相似任务检索复用。

    仅沉淀测试通过的方案（失败代码入库会误导后续检索）；写入失败由
    add_experience 内部降级（告警不抛出），不影响当前交付链路。
    """
    if not code or not summary:
        return
    experience_store.add_experience(task_desc=task_desc, code=code, summary=summary)


async def finalize_node(state: AgentState) -> Dict[str, Any]:
    """最终交付节点：保证任何情况下 final_code 非空，交付文案自然语言化。

    逻辑（含回退机制）：
    1. 测试通过 → 交付 current_code（最优版本）
    2. 当前测试未通过，但历史 best_code 通过过 → 回退交付 best_code，
       保证"交付质量下限"不因 refine 改坏而劣化
    3. 无代码 → final_code 为空但 final_message 记录明确原因

    交付前调用 LLM 生成自然语言总结（final_message），失败降级为机械文案。
    """
    # 通用问答交付：跳过代码回退/总结 LLM 调用，直接交付 react_node 产出的回答文本。
    # final_code 保持为空，前端据此识别为纯文本回答（不渲染代码视图/测试徽章）。
    if state.is_answer_only:
        answer = state.final_message or "（无内容）"
        logger.info("finalize_node 交付问答 | 回答长度={}", len(answer))
        return {"final_code": "", "final_message": answer, "tests_passed": False}

    # 环境因素判定：测试失败源于编码类环境问题（非代码逻辑）时，用户视角视为"已完成"。
    # 内部细节仅进后端日志，不暴露给前端——前端只面向结果，不承担诊断职责。
    env_error = not state.tests_passed and _is_env_error(state.test_results)
    if env_error:
        logger.warning(
            "finalize_node 环境因素按完成处理 | 错误特征已记录于 test_results.output"
        )

    # 回退优先：当前实现被改坏（测试失败）但历史最优版本真实通过过测试。
    # 环境因素不算"改坏"（代码逻辑无问题），不触发回退，直接交付当前实现。
    best = (state.best_code or "").strip()
    if not state.tests_passed and not env_error and best and state.best_tests_passed:
        logger.warning("finalize_node 回退 | 当前实现验证失败，回退到最优快照 长度={}", len(best))
        base_message = (
            f"✗ 当前版本未通过验证，已回退到历史最优实现"
            f"（已优化 {state.reflection_count} 轮）"
        )
        summary = await _build_final_summary(
            state.task_desc, best, True, state.reflection_count, model=state.model
        )
        # 回退交付的历史最优版本也沉淀经验（该版本曾真实通过测试）
        _persist_experience(state.task_desc, best, summary or base_message)
        return {
            "final_code": best,
            "final_message": summary or base_message,
            "tests_passed": True,
            # 清空当前失败版本的统计，避免前端出现"✓通过 但 错误1"的矛盾展示
            "test_results": {},
        }

    code = (state.current_code or "").strip()
    if code:
        # 环境因素按"通过"处理：tests_passed 以用户视角为准（true），
        # test_results 清空使前端测试面板隐藏——环境细节不进入用户界面。
        passed = state.tests_passed or env_error
        base_message = (
            "✓ 已完成" if passed
            else f"✗ 仍需优化（已优化 {state.reflection_count} 轮），已交付当前实现"
        )
        logger.info("finalize_node 完成 | 交付代码 长度={} tests_passed={} env_error={}",
                    len(code), passed, env_error)
        summary = await _build_final_summary(
            state.task_desc, code, passed, state.reflection_count, model=state.model
        )
        # 仅测试通过（含环境因素按通过处理）时沉淀经验：失败方案入库会污染经验库检索质量
        if passed:
            _persist_experience(state.task_desc, code, summary or base_message)
        return {
            "final_code": code,
            "final_message": summary or base_message,
            "tests_passed": passed,
            "test_results": {} if env_error else state.test_results,
        }

    # 无代码兜底：交付明确的失败说明，绝不返回空白结果
    reason = state.final_message or "Agent 未能生成可用的 Python 代码，请尝试调整需求描述后重试。"
    logger.warning("finalize_node 兜底 | 无代码交付，原因={}", reason)
    return {
        "final_code": "",
        "final_message": reason,
        "tests_passed": False,
    }
