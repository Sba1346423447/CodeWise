"""短期对话记忆：管理当前会话的消息历史（追加 / 截断 / 检索）。

消息格式与 OpenAI chat completions 兼容，可直接注入 LLM 请求；
长会话场景由 compress_messages 做滚动摘要压缩（LLM 不可用时降级为硬截断）。
"""

from typing import Dict, List, Optional

from ..llm.client import client
from ..utils.logger import get_logger

logger = get_logger("memory.conversation")

# 上下文消息数上限：超过后触发摘要压缩（与原 max_messages 默认值对齐）
_CONTEXT_MAX_MESSAGES = 20

# 摘要提示词：提炼"需求演进 / 已定决策 / 未解决问题"，供后续轮次作为上下文基底
_COMPRESS_PROMPT = (
    "你是对话摘要器。请把以下多轮对话压缩成一段简洁的中文摘要（200 字以内），"
    "重点保留：用户的核心需求、需求的变化过程、已确定的方案与决策、尚未解决的问题。"
    "直接输出摘要正文，不要任何解释或前缀。\n\n对话内容：\n{content}"
)


class ConversationMemory:
    """当前会话的短期消息历史，使用 OpenAI 兼容 messages 格式。

    超限策略：追加超过 max_messages 时，优先移除最早的非 system 消息，
    保证开头的系统引导提示词始终保留。
    """

    def __init__(self, max_messages: int = 20) -> None:
        self._messages: List[Dict[str, str]] = []
        self.max_messages = max_messages

    def add(self, role: str, content: str) -> None:
        """追加一条消息（role：system / user / assistant / tool）。"""
        self._messages.append({"role": role, "content": content})
        self._enforce_limit()

    def _enforce_limit(self) -> None:
        """超出上限时循环移除最早的非 system 消息。"""
        while len(self._messages) > self.max_messages:
            for idx, msg in enumerate(self._messages):
                if msg["role"] != "system":
                    self._messages.pop(idx)
                    break
            else:  # 极端情况：全部为 system 时直接移除队首
                self._messages.pop(0)

    def truncate(self, max_messages: int) -> None:
        """调整历史上限并立即截断到该长度（保留 system 消息）。"""
        self.max_messages = max_messages
        self._enforce_limit()

    def get_messages(self) -> List[Dict[str, str]]:
        """检索完整消息历史（返回副本，防止外部篡改内部状态）。"""
        return list(self._messages)

    def last(self, n: int) -> List[Dict[str, str]]:
        """检索最近 n 条消息。"""
        return self._messages[-n:]

    def clear(self) -> None:
        """清空当前会话历史（会话重置时调用）。"""
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)


async def compress_messages(
    messages: List[Dict[str, str]], model: str = ""
) -> List[Dict[str, str]]:
    """长会话滚动摘要压缩：消息数超过上限时，把最早部分压成摘要替代直接删除。

    摘要以 system 消息置于上下文开头，保留早期语义（需求演进 / 已定决策），
    避免机械截断导致长对话丢信息。LLM 不可用/失败时降级为硬截断（保留最近 N 条），
    保证任何情况下上下文长度可控、流程不中断。
    """
    if len(messages) <= _CONTEXT_MAX_MESSAGES:
        return messages

    keep = _CONTEXT_MAX_MESSAGES // 2
    head, tail = messages[:-keep], messages[-keep:]
    # 早期消息中排除 system 引导（若有），只把对话正文交给摘要
    body = [m for m in head if m.get("role") != "system"]
    text = "\n".join(f"{m.get('role')}: {m.get('content', '')}" for m in body)

    summary = ""
    try:
        response = await client.chat_or_none(
            messages=[{"role": "system", "content": _COMPRESS_PROMPT.format(content=text)}],
            model=model or None,
        )
        summary = (response.choices[0].message.content or "").strip() if response else ""
    except Exception as exc:
        logger.warning("摘要压缩调用异常，降级为硬截断 | 错误={}", exc)

    if summary:
        logger.info("上下文摘要压缩 | 摘要长度={} 压缩前={} 压缩后={}",
                    len(summary), len(messages), keep + 1)
        return [{"role": "system", "content": f"【此前对话摘要】{summary}"}] + tail

    # 降级：LLM 不可用时硬截断，保留最近 _CONTEXT_MAX_MESSAGES 条
    logger.warning("摘要生成失败，降级为硬截断 | 消息数={}", len(messages))
    return messages[-_CONTEXT_MAX_MESSAGES:]
