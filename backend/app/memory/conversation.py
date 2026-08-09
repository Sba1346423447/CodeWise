"""短期对话记忆：管理当前会话的消息历史（追加 / 截断 / 检索）。

纯标准库实现，消息格式与 OpenAI chat completions 兼容，可直接注入 LLM 请求。
"""

from typing import Dict, List, Optional


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
