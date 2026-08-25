"""repo-map 代码库感知测试：提取签名 / 忽略缓存目录 / 超长截断 / root 不存在返回空串。"""

import pytest

from app.core.repo_map import build_repo_map, load_repo_map_config


class TestBuildRepoMap:
    def test_有py文件_提取类与函数签名(self, tmp_path):
        # 构造一个含类、函数、async 函数、方法的小型模块
        (tmp_path / "mymod.py").write_text(
            "class MyService:\n"
            "    def __init__(self, name: str):\n"
            "        self.name = name\n"
            "    async def run(self, times: int = 1) -> str:\n"
            "        return self.name\n"
            "\n"
            "def helper(a: int, b: int = 0) -> int:\n"
            "    return a + b\n",
            encoding="utf-8",
        )
        out = build_repo_map(str(tmp_path), max_chars=2000)
        assert "mymod.py:" in out
        assert "class MyService" in out
        assert "def __init__(self, name: str)" in out
        assert "async def run(self, times: int = 1) -> str" in out
        assert "def helper(a: int, b: int = 0) -> int" in out

    def test_忽略缓存目录(self, tmp_path):
        # 正常模块 + __pycache__ 下的文件，后者不应出现在摘要中
        (tmp_path / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "app.cpython-313.py").write_text("def leaked():\n    pass\n", encoding="utf-8")
        out = build_repo_map(str(tmp_path), max_chars=2000)
        assert "def main()" in out
        assert "leaked" not in out
        assert "__pycache__" not in out

    def test_忽略venv目录(self, tmp_path):
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "lib.py").write_text("def venv_func():\n    pass\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("def real():\n    pass\n", encoding="utf-8")
        out = build_repo_map(str(tmp_path), max_chars=2000)
        assert "def real()" in out
        assert "venv_func" not in out

    def test_超长截断(self, tmp_path):
        # 生成大量符号使摘要超过 max_chars，应截断并带提示
        lines = [f"def func_{i}(a: int) -> int:\n    return a" for i in range(200)]
        (tmp_path / "big.py").write_text("\n\n".join(lines), encoding="utf-8")
        out = build_repo_map(str(tmp_path), max_chars=200)
        assert out.endswith("... (截断)")
        # 截断后不超预算太多（提示行不计入内容预算）
        assert len(out) < 400

    def test_root不存在_返回空串(self, tmp_path):
        assert build_repo_map(str(tmp_path / "not_exists"), max_chars=2000) == ""

    def test_root为空_返回空串(self):
        assert build_repo_map("", max_chars=2000) == ""

    def test_解析失败文件_跳过不中断(self, tmp_path):
        # 一个语法非法的 .py 文件 + 一个正常文件：非法文件被跳过，正常文件仍输出
        (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
        (tmp_path / "ok.py").write_text("def fine():\n    pass\n", encoding="utf-8")
        out = build_repo_map(str(tmp_path), max_chars=2000)
        assert "def fine()" in out
        assert "broken.py" not in out


class TestLoadRepoMapConfig:
    def test_配置可读取(self):
        cfg = load_repo_map_config()
        # 默认 root 为空（禁用），max_chars 为默认 2000
        assert "root" in cfg
        assert "max_chars" in cfg
        assert int(cfg.get("max_chars", 2000)) >= 0

    def test_读取失败_回退空配置(self, monkeypatch):
        import app.core.repo_map as repo_map

        # 把配置路径指向不存在的文件，应回退为空 dict（不抛异常）
        monkeypatch.setattr(repo_map, "_CONFIG_PATH", repo_map.Path("D:/no_such.yaml"))
        assert load_repo_map_config() == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
