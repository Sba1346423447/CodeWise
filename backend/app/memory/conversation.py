"""短期对话记忆：长会话上下文的滚动摘要压缩。

消息格式与 OpenAI chat completions 兼容，可直接注入 LLM 请求；
会话历史本身由数据库持久化（API 层加载注入），本模块只负责
超限时的摘要压缩（LLM 不可用时降级为硬截断）。
"""

from typing import Dict, List

from ..llm.client import client
from ..utils.logger import get_logger

logger = get_logger("memory.conversation")

# 上下文消息数上限：超过后触发摘要压缩（与原 max_messages 默认值对齐）
_CONTEXT_MAX_MESSAGES = 20

# 摘要提示词：提炼"需求演进 / 已定决策 / 未解决问题"，供后续轮次作为上下文基底
_COMPRESS_PROMPT = (
    "你是对话摘要器。请把以下多轮对话压缩成一段简洁的中文摘要（200 字以内），"
    "重点保留：用户的核心需求、需求的变化过程、已确定的方案与决策、尚未解决的问题。"
    "直接输出摘要正文，不要任何解释或前缀。\n\n对话内容：{content}"
)


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
