"""工具执行层测试：sandbox（截断/沙箱执行/超时）+ code_executor + registry。

sandbox 走真实子进程执行（本机 python，POSIX 资源限制分支在 Windows 自动跳过）。
"""

from app.core.tools.code_executor import CodeExecutor
from app.core.tools.linter import Linter
from app.core.tools.registry import ToolRegistry
from app.utils.sandbox import Sandbox, truncate_output

# ---------- sandbox.truncate_output ----------


def test_truncate_output_short_untouched():
    assert truncate_output("短输出", limit=100) == "短输出"


def test_truncate_output_keeps_head_and_tail():
    text = "A" * 600 + "B" * 600
    out = truncate_output(text, limit=100)
    assert out.startswith("A" * 50)
    assert out.endswith("B" * 50)
    assert "已截断" in out and "1200" in out


# ---------- Sandbox ----------


def test_sandbox_execute_success():
    with Sandbox() as sandbox:
        result = sandbox.execute("print('hello sandbox')")
    assert result["exit_code"] == 0
    assert "hello sandbox" in result["stdout"]
    assert result["timed_out"] is False


def test_sandbox_execute_error_captured():
    with Sandbox() as sandbox:
        result = sandbox.execute("raise ValueError('boom')")
    assert result["exit_code"] != 0
    assert "boom" in result["stderr"]


def test_sandbox_execute_timeout():
    with Sandbox(timeout=2) as sandbox:
        result = sandbox.execute("import time\ntime.sleep(10)")
    assert result["timed_out"] is True
    assert "超时" in result["stderr"]


def test_sandbox_cleanup_idempotent():
    sandbox = Sandbox()
    workdir = sandbox.create()
    sandbox.cleanup()
    sandbox.cleanup()  # 幂等
    import os
    assert not os.path.isdir(workdir)


# ---------- CodeExecutor ----------


def test_code_executor_success():
    result = CodeExecutor().execute(code="print(1 + 1)")
    assert result["success"] is True and "2" in result["stdout"]


def test_code_executor_empty_code():
    assert CodeExecutor().execute(code="")["success"] is False


# ---------- ToolRegistry ----------


def test_registry_register_and_duplicate():
    reg = ToolRegistry()
    reg.register(Linter())
    assert reg.get("linter") is not None
    try:
        reg.register(Linter())
        assert False, "重复注册应报错"
    except ValueError:
        pass


def test_registry_unregister_and_get_missing():
    reg = ToolRegistry()
    reg.register(Linter())
    reg.unregister("linter")
    assert reg.get("linter") is None
    reg.unregister("not-exist")  # 不存在静默忽略


def test_registry_openai_schema():
    reg = ToolRegistry()
    reg.register_many([Linter(), CodeExecutor()])
    schema = reg.to_openai_schema()
    assert {s["function"]["name"] for s in schema} == {"linter", "code_executor"}
    assert schema[0]["type"] == "function"
    assert "parameters" in schema[0]["function"]
