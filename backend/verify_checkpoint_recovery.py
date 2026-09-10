# -*- coding: utf-8 -*-
"""改造三 3.1/3.3 验证：MySQL 检查点持久化 + 进程重启断点恢复。

两阶段两进程演示（--phase all 时自动编排子进程）：
- crash：跑图到 reviewer 子图 code_confirm 挂起（urllib 命中 L1 确认级），
  状态已落 MySQL，然后 os._exit(0) 硬退出（不关连接，模拟进程被杀）
- resume：全新进程、全新图、全新 MySQL 连接（内存零共享，状态只能来自
  MySQL），Command(resume=True) 从子图内部断点继续 → 交付原代码

断言（恢复不重复执行）：
- resume 阶段 react 零调用（LLM 带 tools= 参数的调用计数为 0）——
  若状态丢失会从头跑 react 重生成代码
- 恢复前 aget_state 即读到挂起现场（current_code 非空 + next 非空），
  证明状态确从 MySQL 而来
- final_code 与 crash 阶段生成的代码一致

mock 策略同 verify_e2e_paths.py（side_effect 按调用形态分发，
风险分类 patch nodes 命名空间，不动共享 client 单例）。
"""
import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, ".")
# 脚本不经 main.py 启动，需显式加载 backend/.env（DATABASE_URL）
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")

CODE = "import urllib.request\ndef fetch(url):\n    return urllib.request.urlopen(url).read()\n"
TEST = "def test_stub():\n    assert True\n"


def _mock_response(content="", tool_calls=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.tool_calls = None
    if tool_calls:
        tcs = []
        for name, args in tool_calls:
            tc = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            tcs.append(tc)
        resp.choices[0].message.tool_calls = tcs
    return resp


def _install_llm_mock(llm_calls: list):
    """mock LLM 并记录调用形态（tools=react / text=轻量角色）。"""
    from app.core.graph import nodes as graph_nodes

    async def _side_effect(*args, **kwargs):
        kind = "react" if "tools" in kwargs else "text"
        llm_calls.append(kind)
        content = f"```python\n{CODE}```" if kind == "react" else f"```python\n{TEST}```"
        return _mock_response(content=content)

    graph_nodes.client.chat_or_none = AsyncMock(side_effect=_side_effect)
    graph_nodes.classify_tool_call = AsyncMock(
        return_value={"risk": "safe", "reason": "mock"}
    )


async def _build_graph():
    """全新 MySQL saver + 全新编译图（每阶段各建一次，模拟冷启动）。"""
    from app.core.graph.builder import build_agent_graph
    from app.core.graph.checkpoint import close_mysql_saver, create_mysql_saver

    saver = await create_mysql_saver()
    return build_agent_graph(saver), saver


async def phase_crash(thread_id: str) -> bool:
    """阶段一：跑到 interrupt 挂起后硬退出（检查点已提交 MySQL）。"""
    from app.core.graph.checkpoint import close_mysql_saver

    llm_calls: list = []
    _install_llm_mock(llm_calls)
    graph, saver = await _build_graph()
    cfg = {"configurable": {"thread_id": thread_id}}
    interrupted = False
    async for ns, mode, data in graph.astream(
        {"task_desc": "写抓取函数", "messages": [{"role": "user", "content": "写抓取函数"}]},
        cfg, stream_mode=["updates"], subgraphs=True,
    ):
        if mode == "updates" and "__interrupt__" in data and not ns:
            interrupted = True
    await close_mysql_saver(saver)
    ok = interrupted and "react" in llm_calls
    print(f"[crash] interrupted={interrupted} llm_calls={llm_calls}")
    print(f"[crash] {'PASS' if ok else 'FAIL'} | 状态已落 MySQL，模拟被杀：os._exit(0)")
    if not ok:
        return False
    os._exit(0)  # 硬退出：不经优雅关闭，验证检查点已持久化
    return True  # 不可达


async def phase_resume(thread_id: str) -> bool:
    """阶段二：全新进程恢复——状态只能来自 MySQL。"""
    llm_calls: list = []
    _install_llm_mock(llm_calls)
    graph, saver = await _build_graph()
    cfg = {"configurable": {"thread_id": thread_id}}

    # 恢复前先读现场：证明挂起状态确实从 MySQL 读回（本进程零内存共享）
    snap = await graph.aget_state(cfg)
    has_code = bool(snap.values.get("current_code"))
    pending = bool(snap.next)
    print(f"[resume] 现场读回 | current_code_len={len(snap.values.get('current_code') or '')}"
          f" next={snap.next}")

    from langgraph.types import Command

    result = await graph.ainvoke(Command(resume=True), cfg)

    react_calls = llm_calls.count("react")
    ok = (
        result["final_code"].strip() == CODE.strip()
        and react_calls == 0
        and result["tests_passed"]
        and has_code and pending
    )
    print(f"[resume] llm_calls={llm_calls} react_calls={react_calls}"
          f" tests_passed={result['tests_passed']}")
    print(f"[resume] {'PASS' if ok else 'FAIL'} | 断点续跑且未重复 react 生成")
    from app.core.graph.checkpoint import close_mysql_saver

    await close_mysql_saver(saver)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["crash", "resume", "all"], default="all")
    ap.add_argument("--thread", help="thread_id（all 模式自动生成唯一值传入子进程）")
    args = ap.parse_args()
    import uuid

    thread_id = args.thread or f"demo-recovery-{uuid.uuid4().hex[:8]}"

    if args.phase == "crash":
        asyncio.run(phase_crash(thread_id))
    elif args.phase == "resume":
        sys.exit(0 if asyncio.run(phase_resume(thread_id)) else 1)
    else:
        # 编排：crash 子进程（预期退出码 0）→ resume 子进程（共享 thread_id）
        r1 = subprocess.run([sys.executable, __file__, "--phase", "crash",
                             "--thread", thread_id])
        if r1.returncode != 0:
            print("=== 总体: FAIL（crash 阶段异常）===")
            sys.exit(1)
        r2 = subprocess.run([sys.executable, __file__, "--phase", "resume",
                             "--thread", thread_id])
        print("\n=== 总体:", "ALL PASS" if r2.returncode == 0 else "HAS FAIL", "===")
        sys.exit(0 if r2.returncode == 0 else 1)


if __name__ == "__main__":
    main()
