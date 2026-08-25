"""沙箱管理：创建隔离临时目录，限制执行时间与资源，执行后清理。

仅依赖 Python 标准库；资源限制（CPU / 内存）仅在 POSIX 系统生效，
Windows 下自动跳过，保证跨平台可运行。
"""

import os
import shutil
import subprocess
import sys
import tempfile

# 默认执行超时（秒），环境变量 SANDBOX_TIMEOUT 可覆盖
DEFAULT_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "30"))
# 沙箱根目录，环境变量 SANDBOX_DIR 可覆盖，留空使用系统临时目录
SANDBOX_ROOT = os.getenv("SANDBOX_DIR", "").strip() or tempfile.gettempdir()
# 子进程虚拟内存上限（字节，仅 POSIX 生效）：512MB
MEMORY_LIMIT = 512 * 1024 * 1024
# 工具输出保留上限（字符）：防止超长 stdout/stderr 撑爆 LLM 上下文
OUTPUT_LIMIT = 1000


def truncate_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    """截断工具输出：保留头部 + 尾部，中间省略并标注截断位置。

    设计意图：超长输出（完整日志/大数组）对 LLM 决策价值低，反而稀释注意力；
    保留头部（正常输出）与尾部（错误摘要通常在末尾），并明确告知截断，
    避免模型误判"无错误"。保头保尾策略不丢失关键信息，因此不伤输出质量。
    """
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n...[输出过长，已截断（共 {len(text)} 字符）]...\n{tail}"


class SandboxError(Exception):
    """沙箱相关异常统一类型，便于上层捕获并友好报错。"""


class Sandbox:
    """单个任务隔离沙箱：创建临时工作目录 → 限时执行 → 执行后清理。"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.workdir: str | None = None

    def create(self) -> str:
        """创建隔离临时目录，返回工作目录路径。"""
        self.workdir = tempfile.mkdtemp(prefix="codewise_", dir=SANDBOX_ROOT)
        return self.workdir

    def _apply_resource_limits(self) -> None:
        """在子进程内限制 CPU 时间与虚拟内存（仅 POSIX 生效，Windows 跳过）。"""
        if os.name == "nt":
            return
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (self.timeout, self.timeout))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))

    def execute(self, code: str, filename: str = "main.py") -> dict:
        """在沙箱中执行 Python 代码，返回 stdout/stderr/exit_code/timed_out。"""
        if self.workdir is None:
            self.create()

        script_path = os.path.join(self.workdir, filename)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        kwargs = {}
        if os.name != "nt":
            kwargs["preexec_fn"] = self._apply_resource_limits

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                # errors="replace" 容错 Windows 非 UTF-8 输出（GBK/cp936），避免 _readerthread 崩溃
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                **kwargs,
            )
            return {
                "stdout": truncate_output(proc.stdout),
                "stderr": truncate_output(proc.stderr),
                "exit_code": proc.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            # 超时返回友好提示，不中断整个 Agent 流程
            return {
                "stdout": truncate_output(exc.stdout or ""),
                "stderr": truncate_output((exc.stderr or "") + f"\n[Sandbox] 执行超时（> {self.timeout}s）"),
                "exit_code": -1,
                "timed_out": True,
            }
        except OSError as exc:
            raise SandboxError(f"沙箱执行失败：{exc}") from exc

    def cleanup(self) -> None:
        """清理沙箱临时目录，幂等可重复调用。"""
        if self.workdir and os.path.isdir(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    def __enter__(self) -> "Sandbox":
        self.create()
        return self

    def __exit__(self, *exc_info) -> None:
        self.cleanup()
