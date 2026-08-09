"""代码执行器：在隔离沙箱中执行 Python 代码，捕获 stdout/stderr，返回执行结果。

依赖：utils.sandbox.Sandbox（隔离执行）、.base.Tool（工具基类）。
"""

from typing import Any, Dict

from ...utils.sandbox import Sandbox
from .base import Tool


class CodeExecutor(Tool):
    """在隔离沙箱中运行 Python 代码，返回 stdout / stderr / 退出码 / 是否超时。"""

    name = "code_executor"
    description = (
        "在隔离沙箱中执行 Python 代码，返回 stdout、stderr 与退出码，用于验证代码可运行性。"
    )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "待执行的完整 Python 代码",
                },
            },
            "required": ["code"],
        }

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """在隔离沙箱中执行代码；沙箱自动创建、限时执行并清理。"""
        code = kwargs.get("code", "")
        if not code.strip():
            return {"success": False, "error": "code 参数不能为空"}

        with Sandbox() as sandbox:
            result = sandbox.execute(code)

        return {
            "success": result["exit_code"] == 0,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
        }
