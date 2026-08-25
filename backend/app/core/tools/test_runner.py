"""测试运行器：在隔离沙箱中对生成代码执行 pytest，收集通过/失败/错误详情。

依赖：utils.sandbox（隔离执行 + 输出截断）、.base.Tool（工具基类）；
运行时要求系统可执行 `python -m pytest`。
"""

import os
import re
import subprocess
import sys
from typing import Any

from ...utils.sandbox import Sandbox, truncate_output
from .base import Tool

# pytest 汇总统计：如 "3 passed, 1 failed, 1 error in 1.23s"
_SUMMARY_PATTERN = re.compile(r"(\d+)\s+(passed|failed|error|errors)")


class TestRunner(Tool):
    """在隔离沙箱中运行 pytest，返回通过/失败/错误用例详情。"""

    name = "test_runner"
    description = "对给定的 Python 代码运行 pytest 测试，返回通过/失败/错误的详细结果。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "被测的 Python 代码"},
                "test_code": {"type": "string", "description": "pytest 测试代码"},
            },
            "required": ["code", "test_code"],
        }

    @staticmethod
    def _parse_summary(output: str) -> dict[str, int]:
        """解析 pytest 输出中的用例统计（取最后一次出现的汇总值）。"""
        counts = {"passed": 0, "failed": 0, "errors": 0}
        for match in _SUMMARY_PATTERN.finditer(output[-2000:]):
            key = {"passed": "passed", "failed": "failed", "error": "errors", "errors": "errors"}[
                match.group(2)
            ]
            counts[key] = int(match.group(1))
        return counts

    # 注入头：将被测代码公开符号导入测试命名空间。LLM 生成的测试经常漏写
    # "from solution import X"，导致 NameError 被记为 failed（而非 error），
    # 崩溃分流接不住、反思循环空转。注入后无论 LLM 写不写 import 都能跑
    _INJECT_HEADER = "from solution import *  # 自动注入：被测代码公开符号\n"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        code = kwargs.get("code", "")
        test_code = kwargs.get("test_code", "")
        if not code.strip() or not test_code.strip():
            return {"success": False, "error": "code 与 test_code 参数不能为空"}

        # 测试已显式 import solution 时不重复注入（避免 __all__ 场景下遮蔽意图）
        if "from solution import" not in test_code and "import solution" not in test_code:
            test_code = self._INJECT_HEADER + test_code

        with Sandbox() as sandbox:
            # 被测代码与测试代码写入隔离目录，同一目录保证 import 可见
            with open(os.path.join(sandbox.workdir, "solution.py"), "w", encoding="utf-8") as f:
                f.write(code)
            with open(os.path.join(sandbox.workdir, "test_solution.py"), "w", encoding="utf-8") as f:
                f.write(test_code)

            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "test_solution.py", "-q"],
                    cwd=sandbox.workdir,
                    capture_output=True,
                    text=True,
                    # errors="replace" 容错 Windows 非 UTF-8 输出（GBK/cp936），避免 _readerthread 崩溃
                    encoding="utf-8",
                    errors="replace",
                    timeout=sandbox.timeout,
                )
                # 先在完整输出上解析统计，再截断用于展示：
                # 若先截断，stderr（如插件警告）拼接在 stdout 末尾会把
                # "N passed, M failed" 汇总行挤出保留的尾部，导致统计丢失
                full_output = proc.stdout + proc.stderr
                return {
                    "success": proc.returncode == 0,
                    "exit_code": proc.returncode,
                    **self._parse_summary(full_output),
                    "output": truncate_output(full_output),
                }
            except subprocess.TimeoutExpired as exc:
                return {
                    "success": False,
                    "exit_code": -1,
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "output": (
                        f"[TestRunner] 测试超时（> {sandbox.timeout}s）\n"
                        f"{(exc.stdout or '') + (exc.stderr or '')}"
                    ),
                }
