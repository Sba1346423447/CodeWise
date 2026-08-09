"""工具基类：定义 Tool 抽象接口，所有 Agent 工具继承本基类实现。

纯标准库实现（abc + typing）；子类需实现 name / description / parameters / execute
四个成员，并与 config/tools.yaml 中的工具声明保持一致。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class Tool(ABC):
    """Agent 工具抽象基类：子类必须实现 name / description / parameters / execute。

    说明：execute 为同步方法；若执行耗时（如运行子进程），调用方应使用
    asyncio.to_thread 包装，避免阻塞事件循环。
    """

    # 工具名称，子类覆盖，须与 config/tools.yaml 保持一致
    name: str = ""
    # 工具描述，子类覆盖（供 LLM 判断何时调用本工具）
    description: str = ""

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """返回参数 JSON Schema（OpenAI Function Calling 兼容格式）。"""

    @abstractmethod
    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """执行工具逻辑，返回结构化结果 dict，供 Observation 展示与后续分析。"""
