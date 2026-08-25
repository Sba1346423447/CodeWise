"""静态检查器：基于标准库 ast 的轻量代码质量检查（语法 / 行长 / 尾随空白 / 未使用导入）。

纯标准库实现，不引入 pylint / flake8 等第三方依赖（详见 Linter 类注释）。
"""

import ast
from typing import Any

from .base import Tool

# 最大行长（PEP 8 建议 79，按项目实际放宽）
MAX_LINE_LENGTH = 100


class Linter(Tool):
    """对 Python 代码执行静态检查，返回代码质量问题列表（行号、规则、修改建议）。

    说明：pylint / flake8 未声明在 requirements.txt 中，按依赖约束不新增第三方包，
    本实现基于标准库 ast 与文本规则，覆盖语法、行长、尾随空白、未使用导入四类检查。
    """

    name = "linter"
    description = "对 Python 代码执行静态风格检查，返回代码质量问题列表（行号、规则、修改建议）。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "待检查的 Python 代码"},
            },
            "required": ["code"],
        }

    def _check_syntax(self, code: str) -> list[dict[str, Any]]:
        """语法检查：AST 解析失败即报语法错误。"""
        try:
            ast.parse(code)
            return []
        except SyntaxError as exc:
            return [
                {
                    "line": exc.lineno or 0,
                    "rule": "E0001",
                    "severity": "high",
                    "message": f"语法错误：{exc.msg}",
                }
            ]

    def _check_lines(self, code: str) -> list[dict[str, Any]]:
        """逐行检查：行长超限、行尾多余空白。"""
        issues = []
        for lineno, line in enumerate(code.splitlines(), start=1):
            if len(line) > MAX_LINE_LENGTH:
                issues.append(
                    {
                        "line": lineno,
                        "rule": "E501",
                        "severity": "low",
                        "message": f"行长度 {len(line)} 超过 {MAX_LINE_LENGTH}",
                    }
                )
            if line != line.rstrip():
                issues.append(
                    {
                        "line": lineno,
                        "rule": "W291",
                        "severity": "low",
                        "message": "行尾存在多余空白",
                    }
                )
        return issues

    def _check_unused_imports(self, tree: ast.Module) -> list[dict[str, Any]]:
        """检查导入了但从未引用的名字（F401 风格）。"""
        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[alias.asname or alias.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imported[alias.asname or alias.name] = node.lineno

        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        return [
            {
                "line": lineno,
                "rule": "F401",
                "severity": "medium",
                "message": f"导入了但未使用：{name}",
            }
            for name, lineno in imported.items()
            if name not in used
        ]

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        code = kwargs.get("code", "")
        if not code.strip():
            return {"success": False, "error": "code 参数不能为空"}

        # 语法错误优先返回，后续基于 AST 的检查无意义
        syntax_issues = self._check_syntax(code)
        if syntax_issues:
            return {"success": False, "issue_count": len(syntax_issues), "issues": syntax_issues}

        issues = self._check_lines(code) + self._check_unused_imports(ast.parse(code))
        return {
            "success": len(issues) == 0,
            "issue_count": len(issues),
            "issues": issues,
        }
