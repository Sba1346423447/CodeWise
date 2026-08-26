"""短期对话记忆：长会话上下文的滚动摘要压缩。

消息格式与 OpenAI chat completions 兼容，可直接注入 LLM 请求；
会话历史本身由数据库持久化（API 层加载注入），本模块只负责
超限时的摘要压缩（LLM 不可用时降级为硬截断）。
"""

import asyncio

from ..llm.client import client
from ..utils.logger import get_logger

logger = get_logger("memory.conversation")

# 压缩调用超时（秒）：压缩本应很快，超时即降级硬截断，不白等全局超时
_COMPRESS_TIMEOUT = 40.0

# 上下文消息数上限：超过后触发摘要压缩（与原 max_messages 默认值对齐）
_CONTEXT_MAX_MESSAGES = 20

# 摘要提示词：按固定结构提炼"需求演进 / 已定决策 / 未决问题"，供后续轮次作上下文基底。
# 固定结构保证摘要语义收敛（避免每轮自由发挥导致重点漂移），且可被解析校验
_COMPRESS_PROMPT = (
    '你是对话摘要器。请把以下多轮对话按固定结构压缩成中文摘要，'
    '只输出三行、行首带"需求：" / "已定决策：" / "未决问题："标签，不要任何其他文字。\n'
    "需求：\n已定决策：\n未决问题：\n\n对话内容：{content}"
)

# 三字段标签（用于校验 LLM 摘要是否合规；缺任一行即视为坏摘要）
_SUMMARY_LABELS = ("需求：", "已定决策：", "未决问题：")


def _parse_summary(content: str) -> str:
    """按三字段结构校验摘要；缺任一个标签视为坏摘要，返回空串触发降级。"""
    if all(label in content for label in _SUMMARY_LABELS):
        return content.strip()
    return ""


async def compress_messages(
    messages: list[dict[str, str]], model: str = ""
) -> list[dict[str, str]]:
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
        response = await asyncio.wait_for(
            client.chat_or_none(
                messages=[{"role": "system", "content": _COMPRESS_PROMPT.format(content=text)}],
                model=model or None,
            ),
            timeout=_COMPRESS_TIMEOUT,
        )
        summary = _parse_summary((response.choices[0].message.content or "") if response else "")
    except TimeoutError:
        logger.warning("摘要压缩调用超时（>{}s），降级为硬截断", _COMPRESS_TIMEOUT)
    except Exception as exc:
        logger.warning("摘要压缩调用异常，降级为硬截断 | 错误={}", exc)

    if summary:
        logger.info("上下文摘要压缩 | 摘要长度={} 压缩前={} 压缩后={}",
                    len(summary), len(messages), keep + 1)
        return [{"role": "system", "content": f"【此前对话摘要】{summary}"}] + tail

    # 降级：LLM 不可用时硬截断，保留最近 _CONTEXT_MAX_MESSAGES 条
    logger.warning("摘要生成失败，降级为硬截断 | 消息数={}", len(messages))
    return messages[-_CONTEXT_MAX_MESSAGES:]
