"""文件编辑工具测试：read / write / 越权路径拒绝 / 文件不存在 / root 未配置拒绝。"""

import pytest

from app.core.tools.file_editor import FileEditor


@pytest.fixture
def editor() -> FileEditor:
    return FileEditor()


@pytest.fixture
def root_dir(tmp_path) -> str:
    """把 repo_map.root 指向临时目录，让工具在该目录内可读可写。"""
    return str(tmp_path)


@pytest.fixture(autouse=True)
def _patch_repo_map_root(monkeypatch, root_dir):
    """默认将配置 root 指向临时目录；单个用例可通过 set_root 覆盖。"""
    monkeypatch.setattr(
        "app.core.tools.file_editor.load_repo_map_config",
        lambda: {"root": root_dir, "max_chars": 2000},
    )
    yield root_dir


class TestFileEditorRead:
    def test_read_正常返回(self, editor, root_dir):
        target = __import__("pathlib").Path(root_dir) / "app.py"
        target.write_text("def main():\n    pass\n", encoding="utf-8")
        result = editor.execute(action="read", path="app.py")
        assert result["success"] is True
        assert result["content"] == "def main():\n    pass\n"
        assert result["truncated"] is False

    def test_read_文件不存在(self, editor):
        result = editor.execute(action="read", path="no_such.py")
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_read_超大文件截断(self, editor, root_dir):
        import pathlib

        target = pathlib.Path(root_dir) / "big.txt"
        target.write_text("x" * (100 * 1024 + 100), encoding="utf-8")
        result = editor.execute(action="read", path="big.txt")
        assert result["success"] is True
        assert result["truncated"] is True
        assert len(result["content"]) == 100 * 1024


class TestFileEditorWrite:
    def test_write_创建并写入(self, editor, root_dir):
        import pathlib

        result = editor.execute(
            action="write", path="sub/dir/new.py", content="print('hello')\n"
        )
        assert result["success"] is True
        assert result["bytes"] == len("print('hello')\n".encode("utf-8"))
        written = (pathlib.Path(root_dir) / "sub" / "dir" / "new.py").read_text(encoding="utf-8")
        assert written == "print('hello')\n"

    def test_write_覆盖已有文件(self, editor, root_dir):
        import pathlib

        target = pathlib.Path(root_dir) / "app.py"
        target.write_text("old", encoding="utf-8")
        result = editor.execute(action="write", path="app.py", content="new")
        assert result["success"] is True
        assert (pathlib.Path(root_dir) / "app.py").read_text(encoding="utf-8") == "new"


class TestFileEditorSecurity:
    def test_越权路径_拒绝(self, editor):
        # 注意避开敏感文件关键词（secret/token 等），命中敏感规则会先于越权校验拦截
        result = editor.execute(action="read", path="../outside.txt")
        assert result["success"] is False
        assert "超出项目根目录" in result["error"]

    def test_绝对路径_拒绝(self, editor):
        import os

        # 绕过 root 的绝对路径（Windows / Unix 通用写法：盘符不匹配即为越权）
        result = editor.execute(action="write", path=os.path.abspath("escape.py"), content="x")
        assert result["success"] is False
        assert "超出项目根目录" in result["error"]

    def test_敏感文件env_读取拒绝(self, editor):
        # 第二层工具自检：密钥/凭据文件禁止读写（防 .env 内容泄露给 LLM）
        result = editor.execute(action="read", path=".env")
        assert result["success"] is False
        assert "敏感文件" in result["error"]

    def test_敏感文件pem_写入拒绝(self, editor):
        result = editor.execute(action="write", path="certs/server.pem", content="x")
        assert result["success"] is False
        assert "敏感文件" in result["error"]

    def test_普通文件_不受敏感规则影响(self, editor, root_dir):
        import pathlib

        result = editor.execute(action="write", path="normal.py", content="x = 1")
        assert result["success"] is True
        assert (pathlib.Path(root_dir) / "normal.py").exists()

    def test_root未配置_拒绝(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.tools.file_editor.load_repo_map_config",
            lambda: {"root": "", "max_chars": 2000},
        )
        editor = FileEditor()
        result = editor.execute(action="read", path="app.py")
        assert result["success"] is False
        assert "未配置项目根目录" in result["error"]

    def test_action非法_拒绝(self, editor):
        result = editor.execute(action="delete", path="app.py")
        assert result["success"] is False
        assert "action 必须为 read 或 write" in result["error"]


class TestFileEditorSchema:
    def test_parameters_schema(self, editor):
        params = editor.parameters
        assert params["type"] == "object"
        props = params["properties"]
        assert props["action"]["enum"] == ["read", "write"]
        assert "path" in props
        assert "content" in props
        assert "action" in params["required"]
        assert "path" in params["required"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
