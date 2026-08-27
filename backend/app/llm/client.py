"""LLM 客户端：封装 OpenAI SDK，支持 Function Calling 与流式输出。

依赖：openai（AsyncOpenAI），配置来源见 .config（LLMConfig，支持 YAML + 环境变量）。
"""

import time
from collections.abc import AsyncIterator

from langsmith import traceable
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from ..utils.logger import get_logger
from .config import config

logger = get_logger("llm.client")


class LLMClientError(Exception):
    """LLM 调用统一异常，向调用方暴露可读错误信息。"""


class LLMClient:
    """OpenAI 兼容客户端：非流式对话（Function Calling）+ 流式对话（增量事件）。"""

    def __init__(self) -> None:
        self.model = config.model
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        """懒加载底层客户端：首次调用时校验 API Key，避免导入期抛异常。

        timeout 与 max_retries 显式收紧：
        - timeout 默认 30s：单次请求硬上限，超时立即抛错
        - max_retries=0：不重试，避免 30s 超时 + 30s 重试叠加成 60s 的"假死"
          （用户感知为 Agent 卡死，实际是 SDK 内部在重试）
        异常会通过 agent.py 推送到 SSE error 事件，前端立即显示错误而非无限转圈。
        """
        if self._client is None:
            if not config.api_key:
                raise LLMClientError("未配置 OPENAI_API_KEY，请在 .env 中设置")
            self._client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.request_timeout,
                max_retries=0,
            )
        return self._client

    def _build_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None = None,
    ) -> dict:
        """组装公共请求参数；tools 为空时不传，避免 OpenAI 拒绝空列表。

        model 为空时回退到实例默认模型（config.model），支持按请求动态切换模型。
        """
        kwargs: dict = {
            "model": model or self.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @traceable(run_type="llm", name="llm_chat")
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> ChatCompletion:
        """非流式对话：返回完整响应，调用方自行解析 content 或 tool_calls。"""
        start = time.monotonic()
        n_tools = len(tools) if tools else 0
        try:
            response = await self._get_client().chat.completions.create(
                **self._build_kwargs(messages, tools, model)
            )
            logger.info(
                "LLM 非流式调用成功 | 模型={} 工具数={} 消息数={} 耗时={:.2f}s finish_reason={}",
                model or self.model,
                n_tools,
                len(messages),
                time.monotonic() - start,
                response.choices[0].finish_reason if response.choices else "?",
            )
            return response
        except Exception as exc:
            logger.error(
                "LLM 非流式调用失败 | 模型={} 消息数={} 耗时={:.2f}s 错误={}",
                model or self.model,
                len(messages),
                time.monotonic() - start,
                exc,
            )
            raise LLMClientError(f"LLM 调用失败：{exc}") from exc

    async def chat_or_none(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> ChatCompletion | None:
        """容错对话：LLM 失败/超时返回 None 而非抛异常，供节点降级处理。

        设计意图：反思/优化等辅助环节的超时不应让整个 Agent 流程崩溃。
        调用方拿到 None 后走各自的兜底逻辑（构造兜底 critique / 保留原代码）。
        """
        try:
            return await self.chat(messages, tools, model)
        except LLMClientError:
            logger.warning("LLM 容错调用返回 None（已降级处理）| 消息数={}", len(messages))
            return None

    @traceable(run_type="llm", name="llm_chat_stream")
    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        """流式对话：逐块产出统一事件，供上层 SSE 推送。

        事件格式：
          {"type": "content", "delta": "文本增量"}
          {"type": "tool_calls", "delta": [{"index", "id", "function": {...}}]}
          {"type": "done", "finish_reason": "stop|tool_calls|..."}
        """
        start = time.monotonic()
        try:
            stream = await self._get_client().chat.completions.create(
                **self._build_kwargs(messages, tools), stream=True
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    yield {"type": "content", "delta": delta.content}
                if delta.tool_calls:
                    yield {
                        "type": "tool_calls",
                        "delta": [
                            {
                                "index": tc.index,
                                "id": tc.id,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in delta.tool_calls
                            if tc
                        ],
                    }
                if choice.finish_reason:
                    yield {"type": "done", "finish_reason": choice.finish_reason}
            logger.info(
                "LLM 流式调用成功 | 模型={} 消息数={} 耗时={:.2f}s",
                config.model,
                len(messages),
                time.monotonic() - start,
            )
        except Exception as exc:
            logger.error(
                "LLM 流式调用失败 | 模型={} 消息数={} 耗时={:.2f}s 错误={}",
                config.model,
                len(messages),
                time.monotonic() - start,
                exc,
            )
            raise LLMClientError(f"LLM 流式调用失败：{exc}") from exc


# 模块级单例：全应用共享同一客户端连接
client = LLMClient()
