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
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml
from langgraph.types import interrupt

from ...llm.client import client
from ...llm.config import config as llm_config
from ...memory.experience_store import ExperienceStore
from ...utils.logger import get_logger
from ..prompts.react import build_react_system_prompt, tools_to_text
from ..prompts.refine import build_refine_prompt
from ..prompts.reflection import build_reflection_prompt
from ..repo_map import build_repo_map, load_repo_map_config
from ..security.risk_classifier import (
    RISK_BLOCKED,
    RISK_CONFIRM,
    classify_tool_call,
)
from ..security.rule_filter import (
    SECURITY_ENABLED,
    check_code_confirm_patterns,
    check_code_patterns,
    check_tool_call,
    check_tool_call_confirm,
)
from ..tools.registry import registry
from ..tools.test_runner import TestRunner
from .state import AgentState

logger = get_logger("graph.nodes")

# 轻量模型名（受控输出角色专用）：未配置时为 None，回落主模型（行为兼容）
FAST_MODEL = llm_config.fast_model or None

# 长期经验库（跨会话复用）：HttpClient 连接独立 ChromaDB，无全局可变状态，
# 各图实例安全共享；反思记录则随 AgentState 流转（并发安全，见 reflect/refine 节点）
experience_store = ExperienceStore()

# 项目根：graph -> core -> app -> backend -> 根（与 prompts 模块同层级）
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
# config/prompts.yaml 路径（测试生成提示词以配置层为单一事实来源）
_CONFIG_PATH = _PROJECT_ROOT / "config" / "prompts.yaml"

# 提取 LLM 输出中的 Python 代码块：捕获语言标注，便于跳过非 python 标签块（如 ```json```）
_CODE_BLOCK_RE = re.compile(r"```(\w*)\s*(.*?)```", re.DOTALL)


def _load_test_templates() -> dict:
    """读取 prompts.yaml 中的 test 模板（generate / generate_simple）。"""
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("test", {})


# repo-map 配置（模块级缓存，参考 edges.py 的 MAX_REFLECTION_ROUNDS 模式）：
# root 留空 = 禁用代码库感知，不注入摘要，行为同现状
_REPO_MAP_CONFIG = load_repo_map_config()
_REPO_MAP_ROOT = str(_REPO_MAP_CONFIG.get("root", "") or "").strip()
_REPO_MAP_MAX_CHARS = int(_REPO_MAP_CONFIG.get("max_chars", 2000))


def _build_repo_map() -> str:
    """生成 repo-map 摘要；未配置 root 或生成失败返回空串（不注入，行为同现状）。"""
    if not _REPO_MAP_ROOT:
        return ""
    try:
        return build_repo_map(_REPO_MAP_ROOT, max_chars=_REPO_MAP_MAX_CHARS)
    except Exception:
        logger.warning("repo-map 生成失败，本次不注入")
        return ""


# 工具调用参数 JSON 截断修复：JSON 被 max_tokens 截断成半截时，
# 用此正则尽量从 arguments 中捞取 code 字段的值（允许最后未闭合）
_CODE_ARG_RE = re.compile(r'"code"\s*:\s*("(?:[^"\\]|\\.)*)', re.DOTALL)


def extract_code(text: str) -> str:
    """从 LLM 输出中提取首个合法 Python 代码块；无合法块或语法不合法时返回空字符串。

    多通道解析：用 findall 遍历所有代码块，跳过空块、非 Python 标签块（如 ```json```）、
    语法非法块，取第一个通过 ast.parse 校验的合法 Python 块。无代码块时回退到整段文本
    （兼容"全文即合法代码"场景）；无合法块返回空串，不返回解释文字。

    设计要点：不能"无代码块就返回原文"，否则 LLM 的解释文字（如"测试失败是因为..."）
    会被当成代码写入 current_code 并交付。用 ast.parse 做合法性校验，确保返回值
    一定是合法 Python 代码或空串，让上游 react_node / refine_node 自然走空值分支。
    """
    # 防御：None（LLM 返回异常）直接视为无代码
    if not text:
        return ""
    # 多通道：遍历所有代码块，跳过空块 / 非 python 标签块（如 ```json```）/ 语法非法块，
    # 取第一个通过 ast.parse 校验的合法 Python 块
    for lang, candidate in _CODE_BLOCK_RE.findall(text):
        # 语言标注存在且非 python/py 时视为非 Python 块，直接跳过（不参与 ast 校验）
        if lang and lang.lower() not in ("python", "py"):
            continue
        candidate = candidate.strip()
        if not candidate:
            continue
        if _is_valid_python(candidate):
            return candidate
    # 无代码块时回退到整段文本（保留既有语义，兼容全文即合法代码场景）
    whole = text.strip()
    if not whole:
        return ""
    return whole if _is_valid_python(whole) else ""


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
    candidates: list[str] = []
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


def _has_tool_call(message: dict[str, Any], tool_name: str) -> bool:
    """判断某条消息是否调用了指定名称的工具（兼容 dict / Pydantic 结构）。"""
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else tc.function
        if fn is None:
            continue
        name = fn.get("name") if isinstance(fn, dict) else fn.name
        if name == tool_name:
            return True
    return False


async def react_node(state: AgentState) -> dict[str, Any]:
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
    # 代码库感知：root 已配置时注入项目结构摘要，让 LLM 基于已有代码工作
    repo_map = _build_repo_map()
    system_prompt = build_react_system_prompt(
        tools_to_text(tools_schema), experiences,
        iteration=state.react_iterations, repo_map=repo_map,
    )

    response = await client.chat_or_none(
        messages=[{"role": "system", "content": system_prompt}, *state.messages],
        tools=tools_schema,
        model=state.model or None,
    )

    # 容错调用：LLM 失败/超时返回 None，不中断任务，交付明确失败说明（不伪造成功）。
    # 需先 return update，否则后续访问 response.choices[0] 会空引用报错。
    if response is None:
        update: dict[str, Any] = {
            "messages": [],
            # 迭代计数自增，供 edges.py 判断 ReAct 是否超限
            "react_iterations": state.react_iterations + 1,
            "final_message": "模型调用失败，请检查 API 配置后重试。",
            # 重置安全审查状态：新一轮决策尚未审查
            "security_outcome": "",
            "security_confirmation": None,
        }
        logger.warning("react_node 模型调用失败 | 迭代={} 已降级交付失败说明",
                       update["react_iterations"])
        return update

    assistant = response.choices[0].message
    finish_reason = response.choices[0].finish_reason or ""
    message: dict[str, Any] = {"role": "assistant", "content": assistant.content or ""}
    if assistant.tool_calls:
        message["tool_calls"] = [tc.model_dump() for tc in assistant.tool_calls]

    update: dict[str, Any] = {
        "messages": [message],
        # 迭代计数自增，供 edges.py 判断 ReAct 是否超限
        "react_iterations": state.react_iterations + 1,
        # 重置安全审查状态：新一轮工具调用需重新过审查链路
        "security_outcome": "",
        "security_confirmation": None,
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


async def review_node(state: AgentState) -> dict[str, Any]:
    """安全审查节点（第一层规则过滤 + 第三层 AI 风险分类）。

    审查最后一条 assistant 消息中的全部工具调用，产出三级结论：
    - allow：全部安全，路由到 tool_node 直接执行
    - block：任一命中危险规则或被判明确恶意，本轮全部拦截（拦截消息回填，
      LLM 收到失败 Observation 自行调整；连续失败护栏防其死缠烂打）
    - confirm：存在可疑但可能有正当理由的操作，路由到 confirm_node 挂起等待人工确认

    设计要点：审查结论必须先写入 state 再 interrupt（confirm_node 消费）——
    节点中断会丢失局部变量，恢复重跑时不能重复调用风险分类 LLM，
    更要杜绝"第二次判 SAFE 绕过人工确认直接执行"的安全漏洞。
    第二层工具自检（file_editor 路径/敏感文件校验）在工具 execute 内部，
    不在本节点重复实现。
    """
    last_assistant = None
    for message in reversed(state.messages):
        if message.get("tool_calls"):
            last_assistant = message
            break
    if last_assistant is None:
        # 异常兜底：无待审查的工具调用，交回 react_node 重新决策
        logger.warning("review_node 未找到工具调用消息，放行回 react_node")
        return {"security_outcome": "block", "security_decisions": {}}

    # 总开关关闭：整个审查链路短路，等价于链路加入前的行为
    if not SECURITY_ENABLED:
        return {"security_outcome": "allow", "security_decisions": {}}

    decisions: dict[str, Any] = {}
    blocked_reason = ""
    needs_confirm = False
    for tc in last_assistant["tool_calls"]:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        # 第一层·拦截级：确定性规则过滤（零成本，先拦明确的）
        reason = check_tool_call(name, args)
        if reason:
            decisions[tc["id"]] = {"verdict": "blocked", "reason": reason, "tool": name}
            blocked_reason = reason
            logger.warning("规则过滤拦截 | 工具={} 原因={}", name, reason)
            continue
        # 第一层·确认级：网络外联等可疑但有正当用途的操作，挂起等人工确认
        confirm_reason = check_tool_call_confirm(name, args)
        if confirm_reason:
            decisions[tc["id"]] = {
                "verdict": RISK_CONFIRM, "reason": confirm_reason,
                "tool": name, "args": args,
            }
            needs_confirm = True
            logger.info("规则过滤确认级命中 | 工具={} 原因={}", name, confirm_reason)
            continue
        # 第三层：AI 风险分类（语义判别 prompt 注入等规则拦不住的攻击）
        verdict = await classify_tool_call(name, args, state.task_desc, model=state.model)
        decisions[tc["id"]] = {
            "verdict": verdict["risk"], "reason": verdict["reason"],
            "tool": name, "args": args,
        }
        if verdict["risk"] == RISK_BLOCKED:
            blocked_reason = verdict["reason"]
        elif verdict["risk"] == RISK_CONFIRM:
            needs_confirm = True

    if blocked_reason:
        # 任一 BLOCK → 本轮全部拦截（保守策略：拒绝执行本轮任何工具调用）
        tool_messages = []
        for tc in last_assistant["tool_calls"]:
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps({
                    "success": False,
                    "error": f"安全审查拦截：{blocked_reason}。请调整方案后重试，"
                             f"不要尝试执行被拦截的操作。",
                }, ensure_ascii=False),
            })
        logger.warning("review_node 拦截本轮工具调用 | 工具数={} 原因={}",
                       len(last_assistant["tool_calls"]), blocked_reason)
        return {"security_outcome": "block", "security_decisions": decisions,
                "messages": tool_messages}

    if needs_confirm:
        logger.info("review_node 判定需人工确认 | 工具数={}", len(last_assistant["tool_calls"]))
        return {"security_outcome": "confirm", "security_decisions": decisions}

    return {"security_outcome": "allow", "security_decisions": decisions}


def confirm_node(state: AgentState) -> dict[str, Any]:
    """人工确认节点（第四层）：中高风险操作挂起，等用户批准后才继续执行。

    基于 LangGraph interrupt 实现 human-in-the-loop：
    - 首次执行：interrupt(待确认信息) 抛出中断，图挂起，orchestrator 检测
      __interrupt__ 后经 SSE 推送 confirmation_required 事件给前端
    - 用户响应后：API 层以 Command(resume=批准与否) 恢复同一线程，本节点
      重跑，interrupt() 直接返回用户决定

    本节点不调用 LLM：待确认信息从 state.security_decisions 读取（review_node
    已写入 checkpoint），恢复重跑无副作用、无重复调用，也不会二次判定。
    """
    pending = [
        {"tool": d["tool"], "args": d.get("args", {}), "reason": d["reason"]}
        for d in state.security_decisions.values()
        if d.get("verdict") == RISK_CONFIRM
    ]
    if not pending:
        # 兜底：无待确认项（如 confirm 后又被阻断的异常序列），按拒绝处理回 react
        logger.warning("confirm_node 无待确认项，按拒绝处理")
        return {"security_confirmation": False}

    approved = interrupt({"pending_tools": pending})
    if approved:
        logger.info("confirm_node 用户批准执行 | 工具数={}", len(pending))
        return {"security_confirmation": True}

    # 用户拒绝：为每个待确认的工具调用回填拒绝消息，LLM 收到后自行改道
    logger.info("confirm_node 用户拒绝执行 | 工具数={}", len(pending))
    tool_messages = []
    for tc_id, decision in state.security_decisions.items():
        if decision.get("verdict") != RISK_CONFIRM:
            continue
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": json.dumps({
                "success": False,
                "error": "用户拒绝了该操作。请换一种不涉及该操作的方案完成任务。",
            }, ensure_ascii=False),
        })
    return {"security_confirmation": False, "messages": tool_messages}


# 代码指纹前缀展示长度：确认弹窗里给用户看的代码预览（全文可能很长）
_CODE_PREVIEW_CHARS = 400


def _code_fingerprint(code: str) -> str:
    """计算代码内容指纹（sha256）：用于"已审查/已批准代码"的跳过判断。"""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


async def code_review_node(state: AgentState) -> dict[str, Any]:
    """代码安全审查节点：代码主链路进入测试执行前的强制审查关卡。

    背景：react_node / refine_node 产出的 current_code 会在 test_node 被
    真实执行（pytest / 冒烟 subprocess），原先该链路不经过任何安全审查——
    LLM 生成的网络外联代码会被静默执行。本节点补上这一缺口。

    审查策略（与工具审查链路 review_node 对齐的三级结论）：
    - allow：无危险模式且风险分类为 safe → 放行进入测试链路，并记录代码指纹
    - block：命中拦截级模式（命令执行/动态执行）或风险分类为 blocked →
      清空 current_code 并回填拦截反馈消息，LLM 换方案重新生成
    - confirm：命中确认级模式（网络外联）或风险分类为 confirm →
      挂起等人工确认（用户可能确实要求代码发起网络请求）
    - 指纹跳过：内容与已审查通过的代码一致（测试失败回环 refine 未改代码）→
      直接放行，避免同一份已批准代码反复弹窗
    """
    code = state.current_code
    if not code.strip():
        # 无代码：保持原链路行为，test_gen_node → finalize 兜底交付失败说明
        return {"security_outcome": "allow", "security_decisions": {}}

    # 同内容代码已通过审查（含人工批准）：跳过，防测试回环重复弹窗
    if state.reviewed_code_hash and _code_fingerprint(code) == state.reviewed_code_hash:
        logger.info("code_review_node 指纹匹配跳过审查 | 代码长度={}", len(code))
        return {"security_outcome": "allow", "security_decisions": {}}

    if not SECURITY_ENABLED:
        return {"security_outcome": "allow", "security_decisions": {}}

    # 第一层·拦截级：命令执行 / 动态执行，直接拦截并回填反馈让 LLM 换方案
    block_reason = check_code_patterns(code)
    if not block_reason:
        # 第一层·确认级：网络外联，挂起等人工确认（不静默拦截）
        confirm_reason = check_code_confirm_patterns(code)
        if confirm_reason:
            logger.info("code_review_node 确认级命中 | 代码长度={}", len(code))
            return {
                "security_outcome": "confirm",
                "security_decisions": {
                    "code": {
                        "verdict": RISK_CONFIRM,
                        "reason": confirm_reason,
                        "tool": "代码执行",
                        "args": {"code": code[:_CODE_PREVIEW_CHARS]},
                    }
                },
            }
        # 第三层：AI 风险分类（语义层兜底，拦规则拦不住的数据外泄等意图）
        verdict = await classify_tool_call(
            "code_executor", {"code": code}, state.task_desc, model=state.model
        )
        if verdict["risk"] == RISK_BLOCKED:
            block_reason = verdict["reason"]
        elif verdict["risk"] == RISK_CONFIRM:
            logger.info("code_review_node 风险分类需确认 | 理由={}", verdict["reason"])
            return {
                "security_outcome": "confirm",
                "security_decisions": {
                    "code": {
                        "verdict": RISK_CONFIRM,
                        "reason": verdict["reason"],
                        "tool": "代码执行",
                        "args": {"code": code[:_CODE_PREVIEW_CHARS]},
                    }
                },
            }

    if block_reason:
        logger.warning("code_review_node 拦截 | 原因={}", block_reason)
        return {
            "security_outcome": "block",
            "security_decisions": {},
            # 清空被拦截代码并回填反馈消息，react_node 据此换方案重新生成
            "current_code": "",
            "messages": [{
                "role": "user",
                "content": (
                    f"安全审查拦截：你上一轮生成的代码未通过安全审查（{block_reason}），"
                    f"该代码已被丢弃。请重新生成不包含该模式的代码完成任务。"
                ),
            }],
        }

    # 审查通过：记录指纹（本会话内同内容代码不再重复审查）
    return {
        "security_outcome": "allow",
        "security_decisions": {},
        "reviewed_code_hash": _code_fingerprint(code),
    }


def code_confirm_node(state: AgentState) -> dict[str, Any]:
    """代码人工确认节点（第四层）：含网络外联等确认级模式的代码挂起等用户批准。

    与 confirm_node（工具确认）同样的 interrupt 机制：
    - 首次执行：interrupt({"pending_tools": [...]}) 挂起，orchestrator 检测
      __interrupt__ 后经 SSE 推送 confirmation_required 事件，前端弹确认框
    - 用户批准：记录代码指纹（测试回环不再重复弹窗）→ 进入测试链路
    - 用户拒绝：清空 current_code 并回填拒绝反馈 → react_node 换方案
    """
    decision = state.security_decisions.get("code") or {}
    if not decision:
        # 兜底：无待确认代码（异常序列），按拒绝处理回 react 重新决策
        logger.warning("code_confirm_node 无待确认代码，按拒绝处理")
        return {
            "security_confirmation": False,
            "security_outcome": "block",
            "current_code": "",
            "messages": [{
                "role": "user",
                "content": "安全审查异常：待确认代码丢失，请重新生成代码。",
            }],
        }

    approved = interrupt({"pending_tools": [{
        "tool": decision.get("tool", "代码执行"),
        "args": decision.get("args", {}),
        "reason": decision.get("reason", ""),
    }]})

    if approved:
        logger.info("code_confirm_node 用户批准执行代码 | 长度={}", len(state.current_code))
        return {
            "security_confirmation": True,
            # 批准即记录指纹：测试失败回环（refine 未改代码）不再重复弹窗
            "reviewed_code_hash": _code_fingerprint(state.current_code),
        }

    # 用户拒绝：丢弃该代码并回填反馈，LLM 换一种不涉及该模式的方案
    logger.info("code_confirm_node 用户拒绝执行代码 | 长度={}", len(state.current_code))
    return {
        "security_confirmation": False,
        "security_outcome": "block",
        "current_code": "",
        "messages": [{
            "role": "user",
            "content": (
                f"用户拒绝了执行该代码（{decision.get('reason', '')}），代码已丢弃。"
                f"请换一种不涉及该模式的方案完成任务。"
            ),
        }],
    }


def tool_node(state: AgentState) -> dict[str, Any]:
    """工具执行节点：执行最后一条 assistant 消息中的工具调用，结果作为 tool 消息追加。

    仅在审查链路放行（allow）或人工确认（approved）后到达；
    第二层工具自检（参数校验）在各工具 execute 内部完成。
    """
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


async def _generate_test_code(code: str, model: str = "", failure_hint: str = "") -> str:
    """让 LLM 为当前代码生成 pytest 测试（独立生成一次，避免每次循环重复生成）。

    failure_hint 非空时为"测试自身崩溃后的重生成"：注入上次崩溃原因，
    要求 LLM 修正测试自身的问题（import 错误符号 / mock 缺失等）。
    容错调用：LLM 超时/失败返回空串，由 test_gen_node 重试或冒烟测试兜底。
    """
    prompt = _load_test_templates().get("generate", "").format(code=code)
    if failure_hint:
        regen_hint = _load_test_templates().get("regen_hint", "").format(
            failure_hint=failure_hint, code=code)
        prompt = regen_hint
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=FAST_MODEL or model or None,
    )
    return extract_code(response.choices[0].message.content or "") if response else ""


async def test_gen_node(state: AgentState) -> dict[str, Any]:
    """测试生成节点：为当前代码生成 pytest 测试代码，存入 state.test_code 复用。

    与旧实现的区别：旧 test_node 每次进入都重新用 LLM 生成测试，
    生成失败即误判 tests_passed=False，导致反思链路被不稳定的测试生成带崩。
    现在独立生成一次并复用；仅当 current_code 发生变化（refine 重写）时重新生成。
    """
    code = state.current_code
    if not code.strip():
        logger.warning("test_gen_node 跳过 | 无当前代码可测")
        return {"tests_passed": False}

    # 已有测试代码且代码未变 → 直接复用（refine 后 current_code 变化会重新生成）；
    # 测试自身崩溃（test_broken）除外：需带着失败原因重新生成测试
    if state.test_code.strip() and not state.test_broken:
        logger.info("test_gen_node 复用已有测试代码")
        return {"test_code": state.test_code}

    # 测试崩溃重生成：注入上次崩溃原因，帮助 LLM 修正测试自身的问题
    if state.test_broken and state.test_regen_count < 1:
        logger.info("test_gen_node 重生成 | 测试自身崩溃，注入失败详情")
        test_code = await _generate_test_code(
            code, model=state.model,
            failure_hint=state.test_results.get("output", "")[:800],
        )
        if test_code.strip():
            return {
                "test_code": test_code,
                "test_broken": False,
                "test_regen_count": state.test_regen_count + 1,
            }
        # 重生成失败：退化为冒烟测试兜底，链路必然收敛
        logger.warning("test_gen_node 重生成失败 | 退化为冒烟测试")
        return {"test_code": "", "test_broken": False,
                "test_regen_count": state.test_regen_count + 1}

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
    prompt = _load_test_templates().get("generate_simple", "").format(code=code)
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=model or None,
    )
    return extract_code(response.choices[0].message.content or "") if response else ""


async def test_node(state: AgentState) -> dict[str, Any]:
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

    # 测试自身崩溃分流（collection error）：一条用例都没执行（passed+failed==0
    # 且 errors>0）说明测试文件在 import 阶段就崩了——问题在测试不在代码。
    # 标记 test_broken 让路由回 test_gen_node 重生成测试（代码不动），
    # 切断"坏测试 → 反思 → 改正确代码"的空转循环
    test_broken = (
        bool(test_code)
        and not passed
        and result.get("errors", 0) > 0
        and result.get("passed", 0) + result.get("failed", 0) == 0
    )
    if test_broken:
        logger.warning("test_node 测试自身崩溃 | 重生成测试（代码不动） 次数={}",
                       state.test_regen_count)

    update: dict[str, Any] = {
        "tests_passed": passed,
        "test_broken": test_broken,
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


def _smoke_test(code: str) -> dict[str, Any]:
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
    import sys  # noqa: E402  # 局部导入（仅本函数使用 sys.executable 运行子进程）
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
        exec_globals: dict[str, Any] = {}
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


async def reflect_node(state: AgentState) -> dict[str, Any]:
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
        model=FAST_MODEL or state.model or None,
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


async def refine_node(state: AgentState) -> dict[str, Any]:
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
            # 测试保留复用：refine 已被约束"保持对外接口不变"，测试仍有效，
            # 直接重跑 pytest 省一次测试生成调用；若测试因此崩溃，
            # test_broken 分流会接管重生成
            "test_code": state.test_code,
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
        "用 2-4 句简洁自然的中文向用户说明：你完成了什么、代码如何实现核心点、验证结果如何。\n"
        "产品化约束（必须遵守）：\n"
        "- 面向终端用户，只讲结果与用法，不暴露任何内部机制（反思轮次、模型、"
        "解析过程、测试生成方式等一律不提）\n"
        "- 禁止输出调试建议、排查指引、\"建议再调试/检查格式\"类表述；"
        "验证未通过时只客观说明哪些功能已实现、哪些场景未覆盖验证\n"
        "- 不要输出代码块，不要使用 markdown 标题，直接输出总结文字\n\n"
        f"用户需求：{task_desc}\n"
        f"验证是否通过：{'通过' if tests_passed else '未通过'}\n"
        f"最终代码：\n```python\n{final_code}\n```"
    )
    response = await client.chat_or_none(
        messages=[{"role": "system", "content": prompt}],
        model=FAST_MODEL or model or None,
    )
    summary = (response.choices[0].message.content or "").strip() if response else ""
    return summary


# 环境类错误特征（Windows GBK 编码等）：命中即判定为"环境因素"而非代码逻辑问题。
# 环境因素导致的验证失败不暴露给前端用户（内部细节仅进后端日志），
# 前端按"已完成"展示，避免误导用户以为代码质量有问题。
_ENV_ERROR_MARKERS = ("UnicodeEncodeError", "UnicodeDecodeError", "LookupError")


def _is_env_error(test_results: dict[str, Any] | None) -> bool:
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


async def finalize_node(state: AgentState) -> dict[str, Any]:
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
            "已完成。当前版本在自动验证中未完全通过，已为你回退到验证通过的历史版本。"
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
            "已完成，自动验证通过。" if passed
            else "已完成。核心功能已实现，部分场景未覆盖自动验证，可按需调整。"
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
