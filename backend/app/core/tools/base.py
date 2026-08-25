"""工具基类：定义 Tool 抽象接口，所有 Agent 工具继承本基类实现。

纯标准库实现（abc + typing）；子类需实现 name / description / parameters / execute
四个成员；工具 schema 与 execute 参数强耦合，统一在子类内维护（不外部化配置）。
"""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Agent 工具抽象基类：子类必须实现 name / description / parameters / execute。

    说明：execute 为同步方法；若执行耗时（如运行子进程），调用方应使用
    asyncio.to_thread 包装，避免阻塞事件循环。
    """

    # 工具名称，子类覆盖，作为注册与调用匹配的唯一标识
    name: str = ""
    # 工具描述，子类覆盖（供 LLM 判断何时调用本工具）
    description: str = ""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """返回参数 JSON Schema（OpenAI Function Calling 兼容格式）。"""

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """执行工具逻辑，返回结构化结果 dict，供 Observation 展示与后续分析。"""
