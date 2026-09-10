# -*- coding: utf-8 -*-
"""改造三 3.4 验证：time-travel 状态历史回放 + 检查点 fork 重跑。

流程（mock LLM，内存检查点，不依赖 DB/真实 token）：
1. 基线运行：写 add() → 审查放行 → 测试通过 → 交付（零反思）
2. get_state_history 遍历检查点时间线（含各步骤状态快照）
3. fork：取"coder 刚产出代码"的历史检查点，换测试条件（测试改为失败）
   从该点重跑 → 走反思回环（reflect → refine）→ 交付
4. 对比记录：基线 vs fork 的轨迹与最终状态

fork 语义：ainvoke(None, {thread_id, checkpoint_id}) 从历史检查点
作为新时间线继续执行，不影响原时间线已产出的最终状态。
"""
import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, ".")

GOOD_CODE = "def add(a, b):\n    return a + b\n"
PASS_TEST = "from solution import add\ndef test_add():\n    assert add(2, 3) == 5\n"
FAIL_TEST = "from solution import add\ndef test_add():\n    assert add(2, 3) == 99\n"


def _mock_response(content=""):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.tool_calls = None
    return resp


def _install_llm_mock(mode: str):
    """mode=pass：测试通过（基线）；mode=fail：测试失败（fork 触发反思回环）。"""
    from app.core.graph import nodes as graph_nodes

    async def _side_effect(*args, **kwargs):
        messages = kwargs.get("messages") or (args[0] if args else [])
        if "tools" in kwargs:  # react
            return _mock_response(content=f"```python\n{GOOD_CODE}```")
        prompt = messages[0].get("content", "") if messages else ""
        if "待审查代码" in prompt:  # reflect
            return _mock_response(content="测试未通过，请检查实现")
        if "批判意见" in prompt:  # refine（重给同款代码 → no_progress 收尾）
            return _mock_response(content=f"```python\n{GOOD_CODE}```")
        if "交付总结器" in prompt:  # finalize 总结
            return _mock_response(content="已完成加法函数交付。")
        # test_gen：按 mode 给通过/失败测试
        test = PASS_TEST if mode == "pass" else FAIL_TEST
        return _mock_response(content=f"```python\n{test}```")

    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)
    graph_nodes.classify_tool_call = AsyncMock(
        return_value={"risk": "safe", "reason": "mock"}
    )


async def main():
    from app.core.graph.builder import build_agent_graph

    graph = build_agent_graph()  # 默认 InMemorySaver：本脚本只验 fork 语义
    cfg = {"configurable": {"thread_id": f"demo-timetravel-{uuid.uuid4().hex[:8]}"}}
    task = {"task_desc": "写加法函数", "messages": [{"role": "user", "content": "写加法函数"}]}

    # 1. 基线运行
    _install_llm_mock("pass")
    baseline = await graph.ainvoke(task, cfg)
    print(f"[基线] tests_passed={baseline['tests_passed']}"
          f" reflection_count={baseline['reflection_count']}")

    # 2. 历史时间线：检查点从新到旧
    print("\n[历史检查点] step | next | 关键状态")
    fork_ckpt = None
    async for snap in graph.aget_state_history(cfg):
        has_code = "Y" if snap.values.get("current_code") else "-"
        fin = "Y" if snap.values.get("final_code") else "-"
        print(f"  step={snap.metadata.get('step')} next={snap.next or 'END'}"
              f" code={has_code} finalized={fin}")
        # 取"coder 刚产出代码且未进测试链路"的最早检查点作 fork 起点
        if (snap.values.get("current_code") and not snap.values.get("test_results")
                and fork_ckpt is None):
            fork_ckpt = snap.config["configurable"]["checkpoint_id"]
    if not fork_ckpt:
        print("FAIL：未找到可 fork 的历史检查点")
        sys.exit(1)
    print(f"[fork 起点] checkpoint_id={fork_ckpt[:12]}...（coder 产出代码后）")

    # 3. fork 重跑：换测试条件（失败测试 → 触发反思回环）
    _install_llm_mock("fail")
    fork_cfg = {"configurable": {**cfg["configurable"], "checkpoint_id": fork_ckpt}}
    fork = await graph.ainvoke(None, fork_cfg)
    print(f"\n[fork] tests_passed={fork['tests_passed']}"
          f" reflection_count={fork['reflection_count']}"
          f" final_code_same={fork['final_code'].strip() == GOOD_CODE.strip()}")

    # 4. 对比记录：同一检查点，不同条件 → 轨迹分叉（0 反思 vs 反思回环）
    ok = (
        baseline["tests_passed"] and baseline["reflection_count"] == 0
        and fork["reflection_count"] >= 1
        and fork["final_code"].strip() == GOOD_CODE.strip()
    )
    print("\n=== 总体:", "ALL PASS" if ok else "HAS FAIL", "===")
    sys.exit(0 if ok else 1)


asyncio.run(main())
