"""简化版 repo-map：用 AST 提取项目类/函数签名，生成紧凑代码库摘要。

对标 Aider 的 repo-map 简化实现：不做图排名算法，个人项目规模用全量扫描 + 截断即可。
纯标准库实现（ast / os / pathlib），不引入新依赖。
"""

import ast
import os
import re
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger

logger = get_logger("core.repo_map")

# 项目根：repo_map -> core -> app -> backend -> 根（比 graph/ 下的 edges.py 浅一层）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# config/settings.yaml 路径（repo_map 配置以配置层为单一事实来源）
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"

# 扫描时跳过的目录名
_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}

# 单个文件最多提取的符号条目数（防止超大文件撑爆摘要）
_MAX_ENTRIES_PER_FILE = 50


def load_repo_map_config() -> dict[str, Any]:
    """读取 settings.yaml 的 repo_map 段；读取失败回退默认值（root 空 = 禁用）。

    供 nodes.py（react_node 注入）与 tools/file_editor.py（路径根目录校验）复用。
    """
    try:
        import yaml

        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return raw.get("repo_map", {}) or {}
    except (OSError, TypeError, ValueError):
        return {}


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """生成函数/方法签名文本（含参数类型注解与返回注解）。

    ast.unparse(arguments) 输出不带括号且默认值紧凑（如 `b: str=1`），
    这里统一补齐括号与空格，输出人类可读的完整签名。
    """
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    # unparse 输出逗号后已带空格、默认值紧凑（`b: str=1`）；统一规范化后再拼括号
    args = ast.unparse(node.args)
    args = re.sub(r"\s*=\s*", " = ", args)
    args = re.sub(r"\s*,\s*", ", ", args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args}){ret}"


def _extract_symbols(tree: ast.Module) -> list[str]:
    """提取顶层类（含类内方法签名）与顶层函数签名，输出紧凑符号列表。"""
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            lines.append(f"class {node.name}")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append(f"  {_format_signature(sub)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append(_format_signature(node))
    return lines


def _build_file_summary(rel_path: str, file_path: Path) -> list[str]:
    """解析单个 .py 文件，返回其符号摘要；解析失败跳过（记录 warning），不中断整体。"""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        logger.warning("repo_map 解析失败，跳过：{} | {}", rel_path, exc)
        return []
    return _extract_symbols(tree)


def build_repo_map(root: str, max_chars: int = 2000) -> str:
    """递归扫描 root 下所有 *.py 文件，生成紧凑结构摘要。

    - 跳过 __pycache__ / .venv / venv / node_modules / .git 目录
    - 总长度超过 max_chars 时截断（优先保留整体结构，末尾加截断提示）
    - root 不存在或未配置时返回空串
    """
    if not root:
        return ""
    root_path = Path(root)
    if not root_path.is_dir():
        return ""

    blocks: list[str] = []
    total = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root_path):
        # 原地裁剪 dirnames：os.walk 才会跳过这些子目录
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            abs_path = Path(dirpath) / filename
            rel_path = abs_path.relative_to(root_path).as_posix()
            symbols = _build_file_summary(rel_path, abs_path)
            if not symbols:
                continue
            block = f"{rel_path}:\n  " + "\n  ".join(symbols[:_MAX_ENTRIES_PER_FILE])
            if total + len(block) > max_chars:
                # 超限截断：保留剩余预算内的内容，末尾追加截断提示
                remaining = max_chars - total
                if remaining > 0:
                    blocks.append(block[:remaining])
                truncated = True
                break
            blocks.append(block)
            total += len(block)
        if truncated:
            break

    if not blocks:
        return ""
    if truncated:
        blocks.append("... (截断)")
    return "\n\n".join(blocks)
