"""补充单测：test_runner（沙箱内真实 pytest）+ sse 格式化 + web_search 解析。

全部离线可跑：test_runner 走真实子进程；web_search 仅测纯解析函数
（_clean/_parse_results），不发起 HTTP 请求。
"""

from app.core.tools.test_runner import TestRunner
from app.core.tools.web_search import WebSearch
from app.utils.sse import EVENT_CONFIRMATION, EVENT_DONE, format_sse

# ---------- TestRunner ----------


def test_runner_pass_and_fail():
    code = "def add(a, b):\n    return a + b\n"
    test_code = (
        "from solution import add\n"
        "def test_ok():\n    assert add(1, 2) == 3\n"
        "def test_bad():\n    assert add(1, 1) == 3\n"
    )
    result = TestRunner().execute(code=code, test_code=test_code)
    assert result["success"] is False
    assert result["passed"] == 1 and result["failed"] == 1


def test_runner_all_pass():
    result = TestRunner().execute(
        code="def add(a, b):\n    return a + b\n",
        test_code="from solution import add\ndef test_ok():\n    assert add(1, 2) == 3\n",
    )
    assert result["success"] is True and result["passed"] == 1


def test_runner_auto_inject_solution_namespace():
    # LLM 生成测试常漏写 import：注入 from solution import * 后应直接可跑
    result = TestRunner().execute(
        code="def add(a, b):\n    return a + b\n",
        test_code="def test_ok():\n    assert add(1, 2) == 3\n",
    )
    assert result["success"] is True and result["passed"] == 1


def test_runner_explicit_import_not_duplicated():
    # 测试已显式 import 时不重复注入（不影响原行为）
    result = TestRunner().execute(
        code="def add(a, b):\n    return a + b\n",
        test_code="from solution import add\n\ndef test_ok():\n    assert add(2, 2) == 4\n",
    )
    assert result["success"] is True and result["passed"] == 1


def test_runner_empty_params():
    assert TestRunner().execute(code="", test_code="x")["success"] is False


# ---------- SSE 格式化 ----------


def test_format_sse_event_and_payload():
    msg = format_sse(EVENT_DONE, {"session_id": "s1"})
    assert msg.startswith(f"event: {EVENT_DONE}\n")
    assert '"session_id": "s1"' in msg
    assert msg.endswith("\n\n")


def test_format_sse_confirmation_event():
    msg = format_sse(EVENT_CONFIRMATION, {"run_id": "r1", "tools": []})
    assert "event: confirmation_required" in msg


# ---------- WebSearch 纯解析 ----------


def test_web_search_clean_strips_html():
    assert WebSearch._clean("<b>Hello&nbsp;World</b>") == "Hello World"


def test_web_search_parse_results():
    html = (
        '<a class="result__a" href="https://docs.python.org/1">Title 1</a>'
        '<a class="result__snippet" href="#">Snippet <b>1</b></a>'
        '<a class="result__a" href="https://docs.python.org/2">Title 2</a>'
        '<a class="result__snippet" href="#">Snippet 2</a>'
    )
    results = WebSearch()._parse_results(html)
    assert len(results) == 2
    assert results[0]["title"] == "Title 1"
    assert results[0]["url"] == "https://docs.python.org/1"
    assert results[0]["snippet"] == "Snippet 1"


def test_web_search_empty_query():
    assert WebSearch().execute(query="  ")["success"] is False
