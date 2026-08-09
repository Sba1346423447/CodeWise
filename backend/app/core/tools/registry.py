"""工具注册表：注册/查找/注销工具，并生成 OpenAI Function Calling 兼容的 tools schema。

依赖：.base.Tool（工具抽象基类）；模块级单例 registry 供全局共享。
"""

from typing import Dict, List, Optional

from .base import Tool


class ToolRegistry:
    """管理所有 Agent 工具：按名称注册、查找、注销，并提供 OpenAI schema 输出。"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具；同名工具重复注册直接报错，避免静默覆盖。"""
        if tool.name in self._tools:
            raise ValueError(f"工具已存在：{tool.name}")
        self._tools[tool.name] = tool

    def register_many(self, tools: List[Tool]) -> None:
        """批量注册，供应用启动时一次性装配全部工具。"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """按名称注销工具；不存在时静默忽略。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        """按名称查找工具；未注册返回 None。"""
        return self._tools.get(name)

    def list_names(self) -> List[str]:
        """返回已注册工具名列表。"""
        return list(self._tools.keys())

    def to_openai_schema(self) -> List[dict]:
        """生成 OpenAI Function Calling 兼容的 tools 参数列表（供 chat 请求使用）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]


# 模块级单例：全局共享同一工具注册表
registry = ToolRegistry()
