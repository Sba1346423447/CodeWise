"""文件编辑工具：让 Agent 读取或写入项目文件（文本），对标 Aider 的编辑能力。

安全边界（第二层·工具自检）：
1. 目标路径必须位于配置的项目根目录内（repo_map.root），用 Path.resolve()
   归一化后以 is_relative_to 校验，防止 ../ 越权写入；root 未配置时拒绝操作。
2. 目标路径命中敏感文件规则（.env / 密钥 / 凭据，规则同 security.rule_filter）
   时拒绝读写——路径越权校验挡"界外"，敏感文件防护挡"界内的机密"。
"""

from pathlib import Path
from typing import Any

from ..repo_map import load_repo_map_config
from ..security.rule_filter import check_path_patterns
from .base import Tool

# 单次读取的大小上限（超过则截断并提示）
_READ_LIMIT = 100 * 1024  # 100KB


class FileEditor(Tool):
    """读取或写入项目文件（文本），路径须位于配置的项目根目录内。"""

    name = "file_editor"
    description = "读取或写入项目文件（文本），路径须位于配置的项目根目录内"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "操作类型：read 读取文件内容 / write 写入文件内容",
                },
                "path": {
                    "type": "string",
                    "description": "目标文件相对路径（如 backend/app/main.py）",
                },
                "content": {
                    "type": "string",
                    "description": "写入的完整内容；action=write 时必填，read 时忽略",
                },
            },
            "required": ["action", "path"],
        }

    def _resolve_target(self, path: str) -> tuple[Path | None, str | None]:
        """解析目标路径并做越权校验：必须位于项目根目录内。

        返回 (目标路径, None) 或 (None, 错误信息)。root 未配置时直接拒绝。
        """
        root = str(load_repo_map_config().get("root", "") or "").strip()
        if not root:
            return None, "未配置项目根目录"
        # 敏感文件防护：密钥/凭据/环境变量文件禁止读写（与第一层规则共用规则库）
        sensitive = check_path_patterns(path)
        if sensitive:
            return None, sensitive
        root_path = Path(root).resolve()
        try:
            target = (root_path / path).resolve()
        except (OSError, ValueError):
            return None, "路径解析失败"
        # 越权防护：../ 或绝对路径逃逸出根目录一律拒绝
        if not target.is_relative_to(root_path):
            return None, "路径超出项目根目录"
        return target, None

    def _read(self, target: Path) -> dict[str, Any]:
        """读取文件文本；不存在或读取失败返回 success=False，超大文件截断。"""
        try:
            if not target.is_file():
                return {"success": False, "error": f"文件不存在：{target.name}"}
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"读取失败：{exc}"}
        truncated = len(content) > _READ_LIMIT
        if truncated:
            content = content[:_READ_LIMIT]
        return {
            "success": True,
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "truncated": truncated,
        }

    def _write(self, target: Path, content: str) -> dict[str, Any]:
        """写入文件（目录自动创建）；写入失败返回 success=False。"""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8")
            target.write_bytes(data)
        except OSError as exc:
            return {"success": False, "error": f"写入失败：{exc}"}
        return {"success": True, "bytes": len(data)}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """执行读/写操作；任何异常返回 success=False（不抛异常，与现有工具一致）。"""
        action = str(kwargs.get("action", "") or "").strip()
        path = str(kwargs.get("path", "") or "").strip()
        if action not in ("read", "write"):
            return {"success": False, "error": "action 必须为 read 或 write"}
        if not path:
            return {"success": False, "error": "path 不能为空"}

        target, error = self._resolve_target(path)
        if error:
            return {"success": False, "error": error}

        try:
            if action == "read":
                return self._read(target)
            content = str(kwargs.get("content", "") or "")
            return self._write(target, content)
        except Exception as exc:  # noqa: BLE001 —— 兜底不抛异常
            return {"success": False, "error": f"执行失败：{exc}"}
