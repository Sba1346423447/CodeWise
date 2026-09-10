# -*- coding: utf-8 -*-
"""任务 1.2/1.3 图级端到端行为验证（迁移等价核心路径，mock LLM，不依赖 DB）。

验证四条关键路径：
P1 通用问答：coder 子图 answer_only END → finalize → final_message 交付
P2 代码链路：coder 产出代码 → code_review allow → tester 子图真实 pytest
   → 通过 → finalize → final_code 交付
P3 interrupt 挂起恢复：代码命中确认级模式（urllib.）→ code_review confirm
   → code_confirm interrupt 挂起（astream 检测根级 __interrupt__）
   → Command(resume=True) → 测试链路 → final_code 交付
P4 坏测试回炉（任务 1.3 核心行为）：tester 子图内 collection error
   → test_broken → 子图内回 test_gen 重生成（不经主图）→ 通过 → 交付

mock 策略（关键坑，2026-09-08 排查结论）：
nodes.py 与 risk_classifier.py 共享同一 llm client 单例，先后对
graph_nodes.client / rc.client 各挂 return_value 会互相覆盖（后者胜出），
导致 react 产出物变成风险分类 JSON。正确做法：
- client mock 用 side_effect 按调用形态分发：带 tools= 参数 → react 产物
  （代码块）；单条 system 消息（test_gen / finalize 总结等）→ 自包含测试代码
- 风险分类不 mock client，直接 patch nodes 命名空间的 classify_tool_call
  （review_node / code_review_node 均从该命名空间引用）
P3 不依赖 L3：urllib. 命中 L1 确认级规则（check_code_confirm_patterns）
直接返回 confirm，恰好验证规则层与 interrupt 链路的贯通。
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, ".")

from langgraph.types import Command  # noqa: E402

from app.core.graph import nodes as graph_nodes  # noqa: E402
from app.core.graph.builder import agent_graph  # noqa: E402
from app.core.graph.state import AgentState  # noqa: E402


def _mock_response(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = "stop"
    if tool_calls:
        tcs = []
        for name, args in tool_calls:
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            tcs.append(tc)
        resp.choices[0].message.tool_calls = tcs
    else:
        resp.choices[0].message.tool_calls = None
    return resp


def _stub_llm(react_content, test_content):
    """按调用形态分发的 LLM mock：react（带 tools=）→ 代码块，其余 → 测试代码。"""
    async def _side_effect(*args, **kwargs):
        if "tools" in kwargs:
            return _mock_response(content=react_content)
        return _mock_response(content=test_content)
    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)


def _stub_risk(risk="safe", reason="mock"):
    """patch nodes 命名空间的 L3 风险分类（不动共享 client 单例）。"""
    graph_nodes.classify_tool_call = AsyncMock(
        return_value={"risk": risk, "reason": reason}
    )


async def p1_answer_only():
    """P1：通用问答直接交付。"""
    _stub_llm("闭包是指：函数与其引用的外部变量共同构成的实体", "")
    result = await agent_graph.ainvoke(
        {"task_desc": "解释闭包", "messages": [{"role": "user", "content": "解释闭包"}]},
        {"configurable": {"thread_id": "p1"}},
    )
    ok = "闭包是指" in result["final_message"] and result["final_code"] == ""
    print(f"P1 answer_only: {'PASS' if ok else 'FAIL'} | final_message={result['final_message'][:30]}")
    return ok


async def p2_code_path():
    """P2：代码生成 → 审查（L3 safe）→ 真实 pytest → 交付。"""
    code = "def add(a, b):\n    return a + b\n"
    # 自包含测试：不 import 被测模块也能通过 pytest（P2 关注链路而非测试质量）
    test_code = "def test_add():\n    assert 1 + 1 == 2\n"
    _stub_llm(f"```python\n{code}```", f"```python\n{test_code}```")
    _stub_risk("safe", "纯计算")
    result = await agent_graph.ainvoke(
        {"task_desc": "写加法函数", "messages": [{"role": "user", "content": "写加法函数"}]},
        {"configurable": {"thread_id": "p2"}},
    )
    ok = result["final_code"].strip() == code.strip() and result["tests_passed"]
    print(f"P2 code_path: {'PASS' if ok else 'FAIL'} | tests_passed={result['tests_passed']}"
          f" code_len={len(result['final_code'])}")
    return ok


async def p3_interrupt_resume():
    """P3：确认级代码 → interrupt 挂起 → 批准恢复 → 交付。"""
    code = "import urllib.request\ndef fetch(url):\n    return urllib.request.urlopen(url).read()\n"
    test_code = "def test_stub():\n    assert True\n"
    _stub_llm(f"```python\n{code}```", f"```python\n{test_code}```")
    _stub_risk("safe", "用户授权")
    cfg = {"configurable": {"thread_id": "p3full"}}
    interrupted = False
    async for ns, mode, data in agent_graph.astream(
        {"task_desc": "写抓取函数", "messages": [{"role": "user", "content": "写抓取函数"}]},
        cfg, stream_mode=["updates"], subgraphs=True,
    ):
        if mode == "updates" and "__interrupt__" in data and not ns:
            interrupted = True
    if not interrupted:
        print("P3 interrupt_resume: FAIL | 未检测到根级挂起")
        return False
    # 恢复：批准执行
    result = await agent_graph.ainvoke(Command(resume=True), cfg)
    ok = result["final_code"].strip() == code.strip()
    print(f"P3 interrupt_resume: {'PASS' if ok else 'FAIL'} | interrupted={interrupted}"
          f" code_len={len(result['final_code'])} tests_passed={result['tests_passed']}")
    return ok


async def p4_broken_test_regen():
    """P4：坏测试回炉循环封闭在 tester 子图内（任务 1.3 核心行为）。

    直接驱动独立编译的 tester 子图：第一次生成 collection error 坏测试
    → test_node 判 test_broken → 子图内路由回 test_gen 重生成（好测试）
    → 通过 → 子图 END。断言回炉未出子图（事件全在子图 namespace）。
    """
    from app.core.graph.subgraphs.tester import build_tester_subgraph

    code = "def add(a, b):\n    return a + b\n"
    bad_test = "import missing_module_xyz\ndef test_x():\n    pass\n"
    good_test = "def test_add():\n    assert add(1, 2) == 3\n"
    responses = [
        _mock_response(content=f"```python\n{bad_test}```"),
        _mock_response(content=f"```python\n{good_test}```"),
    ]

    async def _side_effect(*args, **kwargs):
        return responses.pop(0)

    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)
    tester = build_tester_subgraph()
    result = await tester.ainvoke(
        AgentState(current_code=code, task_desc="写加法函数"),
        {"configurable": {"thread_id": "p4"}},
    )
    ok = (
        result["tests_passed"] is True
        and result["test_regen_count"] == 1
        and result["test_broken"] is False
    )
    print(f"P4 broken_test_regen: {'PASS' if ok else 'FAIL'}"
          f" | tests_passed={result['tests_passed']}"
          f" regen_count={result['test_regen_count']}")
    return ok


async def p5_interrupt_reject():
    """P5：interrupt 拒绝路径（任务 1.4）：confirm 挂起 → 用户拒绝
    → code_confirm 写 block 并清空代码 → 主图 route_after_reviewer
    回 coder → react 凭拒绝反馈换安全方案 → 审查放行 → 交付。
    """
    bad_code = "import urllib.request\ndef fetch(url):\n    return urllib.request.urlopen(url).read()\n"
    safe_code = "def greet():\n    return 'hello'\n"
    test_code = "def test_stub():\n    assert True\n"
    react_calls = {"n": 0}

    async def _side_effect(*args, **kwargs):
        if "tools" in kwargs:
            react_calls["n"] += 1
            content = bad_code if react_calls["n"] == 1 else safe_code
            return _mock_response(content=f"```python\n{content}```")
        return _mock_response(content=f"```python\n{test_code}```")

    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)
    _stub_risk("safe", "mock")
    cfg = {"configurable": {"thread_id": "p5"}}
    # 第一次运行：在 reviewer 子图 code_confirm_node 挂起
    async for ns, mode, data in agent_graph.astream(
        {"task_desc": "写抓取函数", "messages": [{"role": "user", "content": "写抓取函数"}]},
        cfg, stream_mode=["updates"], subgraphs=True,
    ):
        pass  # 挂起即停，无需消费事件
    # 恢复：拒绝执行 → coder 换安全方案 → 交付
    result = await agent_graph.ainvoke(Command(resume=False), cfg)
    ok = result["final_code"].strip() == safe_code.strip()
    print(f"P5 interrupt_reject: {'PASS' if ok else 'FAIL'}"
          f" | code_len={len(result['final_code'])} react_calls={react_calls['n']}")
    return ok


async def p6_reflection_loop():
    """P6：反思回环（任务 1.5 supervisor 多轮循环核心路径）：
    react 产出坏代码 → 真实测试失败 → reflect 产 critique → coder 凭
    critique 进 refine 修好 → 重新审查/测试 → 通过交付。
    验证 supervisor 的 tester→reflect→coder→reviewer→tester 多轮派发。
    """
    bad_code = "def add(a, b):\n    return a - b\n"
    good_code = "def add(a, b):\n    return a + b\n"
    test_code = "from solution import add\ndef test_add():\n    assert add(2, 3) == 5\n"

    async def _side_effect(*args, **kwargs):
        messages = kwargs.get("messages") or (args[0] if args else [])
        if "tools" in kwargs:
            return _mock_response(content=f"```python\n{bad_code}```")
        prompt = messages[0].get("content", "") if messages else ""
        if "待审查代码" in prompt:  # reflect
            return _mock_response(content="加法实现写成了减法，应返回 a + b")
        if "批判意见" in prompt:  # refine
            return _mock_response(content=f"```python\n{good_code}```")
        if "交付总结器" in prompt:  # finalize 总结
            return _mock_response(content="已修复加法逻辑，验证通过。")
        return _mock_response(content=f"```python\n{test_code}```")  # test_gen

    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)
    _stub_risk("safe", "纯计算")
    result = await agent_graph.ainvoke(
        {"task_desc": "写加法函数", "messages": [{"role": "user", "content": "写加法函数"}]},
        {"configurable": {"thread_id": "p6"}},
    )
    ok = (
        result["final_code"].strip() == good_code.strip()
        and result["tests_passed"]
        and result["reflection_count"] >= 1
    )
    print(f"P6 reflection_loop: {'PASS' if ok else 'FAIL'}"
          f" | code_len={len(result['final_code'])}"
          f" reflection_count={result['reflection_count']}"
          f" tests_passed={result['tests_passed']}")
    return ok


async def main():
    r1 = await p1_answer_only()
    r2 = await p2_code_path()
    r3 = await p3_interrupt_resume()
    r4 = await p4_broken_test_regen()
    r5 = await p5_interrupt_reject()
    r6 = await p6_reflection_loop()
    print("\n=== 总体:",
          "ALL PASS" if all([r1, r2, r3, r4, r5, r6]) else "HAS FAIL", "===")


asyncio.run(main())
