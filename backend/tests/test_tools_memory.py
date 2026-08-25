"""工具与记忆模块测试：linter（纯逻辑）+ conversation（摘要压缩/降级截断）。

conversation 通过 monkeypatch 替换 LLM 客户端，验证摘要成功与降级两条路径，
不发起真实 LLM 请求。
"""

from unittest.mock import AsyncMock, MagicMock

from app.core.tools.linter import Linter
from app.memory import conversation

# ---------- Linter ----------


def test_linter_clean_code():
    result = Linter().execute(code="import os\nprint(os.getcwd())\n")
    assert result["success"] is True and result["issue_count"] == 0


def test_linter_syntax_error():
    result = Linter().execute(code="def broken(:\n    pass\n")
    assert result["success"] is False
    assert result["issues"][0]["rule"] == "E0001"


def test_linter_long_line_and_trailing_whitespace():
    code = "x = 1  \n" + "y = '" + "a" * 120 + "'\n"
    result = Linter().execute(code=code)
    rules = {i["rule"] for i in result["issues"]}
    assert "W291" in rules and "E501" in rules


def test_linter_unused_import():
    result = Linter().execute(code="import os\nimport sys\nprint(sys.argv)\n")
    assert result["issue_count"] == 1
    assert result["issues"][0]["rule"] == "F401"
    assert "os" in result["issues"][0]["message"]


def test_linter_empty_code():
    assert Linter().execute(code="  ")["success"] is False


# ---------- conversation.compress_messages ----------


def _messages(n: int) -> list[dict[str, str]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
            for i in range(n)]


async def test_compress_short_context_untouched(monkeypatch):
    """未超上限直接原样返回（不触发 LLM）。"""
    calls = AsyncMock()
    monkeypatch.setattr(conversation.client, "chat_or_none", calls)
    messages = _messages(10)
    assert await conversation.compress_messages(messages) == messages
    calls.assert_not_called()


async def test_compress_with_summary(monkeypatch):
    """超上限且 LLM 可用：早期消息压成摘要 system 消息 + 保留尾部。"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "用户要求实现快排并优化性能"
    monkeypatch.setattr(
        conversation.client, "chat_or_none", AsyncMock(return_value=response)
    )
    result = await conversation.compress_messages(_messages(40))
    assert result[0]["role"] == "system"
    assert "此前对话摘要" in result[0]["content"]
    assert len(result) == 11  # 1 摘要 + 10 尾部保留


async def test_compress_fallback_to_truncation(monkeypatch):
    """LLM 失败时降级：硬截断保留最近 N 条，流程不中断。"""
    monkeypatch.setattr(
        conversation.client, "chat_or_none", AsyncMock(side_effect=RuntimeError("LLM 不可用"))
    )
    result = await conversation.compress_messages(_messages(40))
    assert len(result) == 20
    assert result[-1]["content"] == "消息39"


async def test_compress_llm_returns_none(monkeypatch):
    """LLM 返回 None（chat_or_none 静默失败）：同样降级硬截断。"""
    monkeypatch.setattr(conversation.client, "chat_or_none", AsyncMock(return_value=None))
    result = await conversation.compress_messages(_messages(30))
    assert len(result) == 20
