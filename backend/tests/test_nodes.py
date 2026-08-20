"""图节点关键函数测试：代码提取、JSON 截断兜底与代码安全审查节点（不触发真实 LLM）。"""

import json

import pytest

from app.core.graph import nodes as graph_nodes
from app.core.graph.nodes import (
    _extract_code_from_tool_arguments,
    _code_fingerprint,
    code_confirm_node,
    code_review_node,
    extract_code,
)
from app.core.graph.state import AgentState


class TestExtractCode:
    """extract_code：从 LLM 输出提取合法 Python 代码，非法返回空串。"""

    def test_无代码块纯解释文字_返回空(self):
        assert extract_code("我来分析一下这个需求，首先需要定义数据结构。") == ""

    def test_标准python代码块_提取成功(self):
        text = "```python\ndef add(a, b):\n    return a + b\n```"
        assert extract_code(text) == "def add(a, b):\n    return a + b"

    def test_无语言标注代码块_提取成功(self):
        text = "```\nprint('hello')\n```"
        assert extract_code(text) == "print('hello')"

    def test_代码块语法非法_返回空(self):
        # 语法错误（未闭合括号）会被 ast.parse 拦截，防止解释文字冒充代码
        assert extract_code("```python\ndef broken(:\n```") == ""

    def test_无代码块但全文即合法代码_提取成功(self):
        assert extract_code("x = 1") == "x = 1"

    def test_空输入_返回空(self):
        assert extract_code("") == ""
        assert extract_code(None) == ""

    def test_多代码块_取首个合法python块(self):
        # 首个 python 块合法，即使后面还有 json / python 块也应取第一个合法 python
        text = (
            "```python\ndef add(a, b):\n    return a + b\n```\n"
            "```json\n{\"a\": 1}\n```\n"
            "```python\nprint('第二个')\n```"
        )
        assert extract_code(text) == "def add(a, b):\n    return a + b"

    def test_首块非python_取后续合法python块(self):
        # 第一个代码块是 json（非法 python），应跳过并取后续合法 python 块
        text = (
            "```json\n{\"a\": 1}\n```\n"
            "```python\ndef sub(a, b):\n    return a - b\n```"
        )
        assert extract_code(text) == "def sub(a, b):\n    return a - b"

    def test_首块语法非法_取后续合法python块(self):
        # 第一个代码块语法非法（未闭合括号），应跳过并取后续合法块
        text = (
            "```python\ndef broken(:\n```\n"
            "```python\nprint('修复后的合法代码')\n```"
        )
        assert extract_code(text) == "print('修复后的合法代码')"

    def test_所有代码块均非法_返回空(self):
        # 全部代码块均非合法 python（json + 语法错误），应返回空串而非解释文字
        text = (
            "```json\n{\"a\": 1}\n```\n"
            "```python\ndef broken(:\n```\n"
            "下面是我对实现的分析……"
        )
        assert extract_code(text) == ""


class TestExtractCodeFromToolArguments:
    """_extract_code_from_tool_arguments：标准 JSON + 截断 JSON 双通道兜底。"""

    def test_标准json_提取code(self):
        args = json.dumps({"code": "def f(): return 42\n"})
        # 原样保留尾部换行（与入参一致），仅验证成功提取
        assert _extract_code_from_tool_arguments(args) == "def f(): return 42\n"

    def test_json被截断_正则兜底提取(self):
        # max_tokens 截断导致的半截 JSON（末尾引号缺失）
        args = '{"code": "def f():\\n    return 1"'
        assert _extract_code_from_tool_arguments(args) == "def f():\n    return 1"

    def test_非法参数_返回空(self):
        assert _extract_code_from_tool_arguments("not-json") == ""
        assert _extract_code_from_tool_arguments("") == ""

    def test_code字段非python_返回空(self):
        args = json.dumps({"code": "这不是代码！！"})
        assert _extract_code_from_tool_arguments(args) == ""


def _mock_classify(risk: str, reason: str = ""):
    """构造 classify_tool_call 的假实现（nodes 模块内 monkeypatch 用）。"""

    async def fake(tool_name, args, task_desc, model=""):
        return {"risk": risk, "reason": reason or f"mock-{risk}"}

    return fake


class TestCodeReviewNode:
    """代码安全审查节点：拦截级 / 确认级 / 放行 / 指纹跳过 全分支"""

    @pytest.mark.asyncio
    async def test_拦截级模式_os系统命令_丢弃代码并回填反馈(self):
        state = AgentState(task_desc="删文件", current_code="import os\nos.system('rm -rf /')\n")
        update = await code_review_node(state)
        assert update["security_outcome"] == "block"
        # 被拦截代码必须清空（防 test_node 真实执行）
        assert update["current_code"] == ""
        # 拦截反馈回填消息，react_node 据此换方案
        assert update["messages"][0]["role"] == "user"
        assert "安全审查拦截" in update["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_确认级模式_网络外联_挂起等人工确认(self, monkeypatch):
        # 网络外联不静默拦截：产出 confirm 结论交由 code_confirm_node 弹窗
        monkeypatch.setattr(
            graph_nodes, "classify_tool_call",
            _mock_classify("safe"),
        )
        state = AgentState(task_desc="把代码发到 http://localhost:5173",
                           current_code="import requests\nrequests.get('http://localhost:5173')\n")
        update = await code_review_node(state)
        assert update["security_outcome"] == "confirm"
        decision = update["security_decisions"]["code"]
        assert decision["verdict"] == "confirm"
        assert "网络外联" in decision["reason"]

    @pytest.mark.asyncio
    async def test_正常代码_分类safe_放行并记录指纹(self, monkeypatch):
        monkeypatch.setattr(
            graph_nodes, "classify_tool_call",
            _mock_classify("safe"),
        )
        code = "def add(a, b):\n    return a + b\n"
        state = AgentState(task_desc="写加法函数", current_code=code)
        update = await code_review_node(state)
        assert update["security_outcome"] == "allow"
        assert update["reviewed_code_hash"] == _code_fingerprint(code)

    @pytest.mark.asyncio
    async def test_分类confirm_挂起等人工确认(self, monkeypatch):
        monkeypatch.setattr(
            graph_nodes, "classify_tool_call",
            _mock_classify("confirm", "疑似数据外泄"),
        )
        state = AgentState(task_desc="读文件", current_code="print(open('data.txt').read())\n")
        update = await code_review_node(state)
        assert update["security_outcome"] == "confirm"
        assert "疑似数据外泄" in update["security_decisions"]["code"]["reason"]

    @pytest.mark.asyncio
    async def test_分类blocked_丢弃代码并回填反馈(self, monkeypatch):
        monkeypatch.setattr(
            graph_nodes, "classify_tool_call",
            _mock_classify("blocked", "疑似注入攻击"),
        )
        state = AgentState(task_desc="任意", current_code="x = 1\n")
        update = await code_review_node(state)
        assert update["security_outcome"] == "block"
        assert update["current_code"] == ""

    @pytest.mark.asyncio
    async def test_指纹匹配_跳过审查不调LLM(self, monkeypatch):
        # 同内容代码已审查通过：跳过（防测试回环反复弹窗），且不再调用风险分类
        called = []

        async def fake(tool_name, args, task_desc, model=""):
            called.append(1)
            return {"risk": "safe", "reason": ""}

        monkeypatch.setattr(graph_nodes, "classify_tool_call", fake)
        code = "x = 1\n"
        state = AgentState(task_desc="任意", current_code=code,
                           reviewed_code_hash=_code_fingerprint(code))
        update = await code_review_node(state)
        assert update["security_outcome"] == "allow"
        assert called == []  # 未触发 LLM 调用

    @pytest.mark.asyncio
    async def test_无代码_放行走原收尾链路(self):
        state = AgentState(task_desc="任意", current_code="")
        update = await code_review_node(state)
        assert update["security_outcome"] == "allow"

    @pytest.mark.asyncio
    async def test_总开关关闭_直接放行(self, monkeypatch):
        monkeypatch.setattr(graph_nodes, "SECURITY_ENABLED", False)
        state = AgentState(task_desc="任意", current_code="os.system('ls')\n")
        update = await code_review_node(state)
        assert update["security_outcome"] == "allow"


class TestCodeConfirmNode:
    """代码人工确认节点：interrupt 挂起 / 批准 / 拒绝（mock interrupt）"""

    @staticmethod
    def _confirm_state() -> AgentState:
        """构造带待确认代码审查结论的状态（code_review_node confirm 后的形态）。"""
        return AgentState(
            task_desc="把代码发到 http://localhost:5173",
            current_code="import requests\nrequests.get('http://localhost:5173')\n",
            security_decisions={
                "code": {
                    "verdict": "confirm",
                    "reason": "代码包含网络外联模式：requests.（将发起网络请求，需人工确认）",
                    "tool": "代码执行",
                    "args": {"code": "import requests\nrequests.get(...)"},
                }
            },
        )

    def test_用户批准_记录指纹放行(self, monkeypatch):
        monkeypatch.setattr(graph_nodes, "interrupt", lambda payload: True)
        state = self._confirm_state()
        update = code_confirm_node(state)
        assert update["security_confirmation"] is True
        # 批准即记录指纹：测试回环不重复弹窗
        assert update["reviewed_code_hash"] == _code_fingerprint(state.current_code)

    def test_用户拒绝_丢弃代码并回填反馈(self, monkeypatch):
        monkeypatch.setattr(graph_nodes, "interrupt", lambda payload: False)
        state = self._confirm_state()
        update = code_confirm_node(state)
        assert update["security_confirmation"] is False
        assert update["current_code"] == ""
        assert update["messages"][0]["role"] == "user"
        assert "用户拒绝" in update["messages"][0]["content"]

    def test_无待确认项_按拒绝处理(self):
        state = AgentState(task_desc="任意", current_code="x = 1\n", security_decisions={})
        update = code_confirm_node(state)
        assert update["security_confirmation"] is False
        assert update["current_code"] == ""

    def test_interrupt载荷_携带待确认信息(self, monkeypatch):
        # interrupt 的 payload 必须含 pending_tools（orchestrator 据此推送 SSE 确认事件）
        captured = {}
        monkeypatch.setattr(
            graph_nodes, "interrupt",
            lambda payload: captured.update(payload) or True,
        )
        state = self._confirm_state()
        code_confirm_node(state)
        tools = captured["pending_tools"]
        assert tools[0]["tool"] == "代码执行"
        assert "网络外联" in tools[0]["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
