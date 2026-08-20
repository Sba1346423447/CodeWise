"""停止生成端点测试：POST /api/agent/stop 的注册表查找与任务取消行为。

不启动 HTTP 服务，直接调用 stop_agent 并操纵 _RUNNING_TASKS 注册表：
- 无运行任务（不存在/已结束）→ 幂等返回 stopped=False，不抛错
- 有运行任务 → task.cancel() 生效，返回 stopped=True
"""

import asyncio

import pytest

from app.api.agent import StopRequest, _RUNNING_TASKS, stop_agent


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空注册表，避免用例间任务泄漏。"""
    _RUNNING_TASKS.clear()
    yield
    for task in list(_RUNNING_TASKS.values()):
        if not task.done():
            task.cancel()
    _RUNNING_TASKS.clear()


async def _sleep_forever() -> None:
    """永不结束的占位任务：模拟运行中的 Agent 图执行。"""
    await asyncio.sleep(3600)


class TestStopAgent:
    @pytest.mark.asyncio
    async def test_无运行任务_幂等返回False(self):
        result = await stop_agent(StopRequest(run_id="no-such-run"))
        assert result == {"stopped": False}

    @pytest.mark.asyncio
    async def test_已结束任务_返回False(self):
        # 已完成的任务仍在注册表（注销由 finally 异步完成前）→ 视为无可停止
        done_task = asyncio.get_running_loop().create_task(asyncio.sleep(0))
        await done_task
        _RUNNING_TASKS["run-done"] = done_task
        result = await stop_agent(StopRequest(run_id="run-done"))
        assert result == {"stopped": False}

    @pytest.mark.asyncio
    async def test_运行中任务_取消并返回True(self):
        task = asyncio.get_running_loop().create_task(_sleep_forever())
        _RUNNING_TASKS["run-1"] = task
        result = await stop_agent(StopRequest(run_id="run-1"))
        assert result == {"stopped": True}
        # cancel 已请求：任务进入取消流程（抛 CancelledError，sleep 被中断）
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_重复停止_第二次返回False(self):
        # 与前端竞态对齐：第一次停止成功后任务已取消/结束，重复点击不报错
        task = asyncio.get_running_loop().create_task(_sleep_forever())
        _RUNNING_TASKS["run-2"] = task
        assert (await stop_agent(StopRequest(run_id="run-2")))["stopped"] is True
        await asyncio.sleep(0)  # 让取消信号注入任务
        assert (await stop_agent(StopRequest(run_id="run-2")))["stopped"] is False
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_停止一个任务_不影响其他任务(self):
        other = asyncio.get_running_loop().create_task(_sleep_forever())
        target = asyncio.get_running_loop().create_task(_sleep_forever())
        _RUNNING_TASKS["run-other"] = other
        _RUNNING_TASKS["run-target"] = target
        await stop_agent(StopRequest(run_id="run-target"))
        # 目标任务被取消，其他任务不受影响
        with pytest.raises(asyncio.CancelledError):
            await target
        assert not other.done()
        other.cancel()
        with pytest.raises(asyncio.CancelledError):
            await other
