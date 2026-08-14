"""图节点关键函数测试：代码提取与 JSON 截断兜底（不触发 LLM 调用）。"""

import json

import pytest

from app.core.graph.nodes import _extract_code_from_tool_arguments, extract_code


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
