"""改造三 3.1 单测：checkpoint 工厂配置解析与 builder 参数化（不依赖 MySQL）。"""

from pathlib import Path

import pytest

from app.core.graph import checkpoint as cp
from app.core.graph.builder import build_agent_graph


# ---------- 配置模式读取 ----------

def test_mode_default_memory():
    """真实 settings.yaml 默认 memory（保证测试/无 DB 环境零依赖）。"""
    assert cp.CHECKPOINTER_MODE == "memory"


def test_mode_mysql_via_config(tmp_path, monkeypatch):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("agent:\n  checkpointer: mysql\n", encoding="utf-8")
    monkeypatch.setattr(cp, "_CONFIG_PATH", cfg)
    assert cp._load_checkpointer_mode() == "mysql"


@pytest.mark.parametrize("raw", ["agent:\n  checkpointer: redis\n", "agent: {}\n", ""])
def test_mode_invalid_falls_back_memory(tmp_path, monkeypatch, raw):
    """未知值 / 缺项 / 空配置回退 memory（不因配置笔误启动失败）。"""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(cp, "_CONFIG_PATH", cfg)
    assert cp._load_checkpointer_mode() == "memory"


# ---------- DATABASE_URL 解析 ----------

def test_parse_url_full():
    from unittest.mock import patch

    with patch.dict("os.environ", {"DATABASE_URL": "mysql+aiomysql://u:p@10.0.0.1:3307/cwdb"}):
        params = cp._parse_database_url()
    assert params == {"host": "10.0.0.1", "port": 3307, "user": "u",
                      "password": "p", "db": "cwdb"}


def test_parse_url_defaults():
    from unittest.mock import patch

    with patch.dict("os.environ", {"DATABASE_URL": "mysql+aiomysql://root@"}):
        params = cp._parse_database_url()
    assert params["host"] == "127.0.0.1" and params["port"] == 3306


def test_parse_url_missing_raises():
    from unittest.mock import patch

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            cp._parse_database_url()


def test_parse_url_bad_scheme_raises():
    from unittest.mock import patch

    with patch.dict("os.environ", {"DATABASE_URL": "postgres://x/y"}):
        with pytest.raises(RuntimeError, match="mysql"):
            cp._parse_database_url()


# ---------- builder 参数化 ----------

def test_build_graph_default_memory_saver():
    """默认（None）编译图使用 InMemorySaver（测试兼容不变）。"""
    graph = build_agent_graph()
    assert type(graph.checkpointer).__name__ == "InMemorySaver"


def test_build_graph_accepts_custom_checkpointer():
    """传入自定义 saver 编译图（lifespan 重建 MySQL 单例的依据）。

    LangGraph 校验 checkpointer 须为 BaseCheckpointSaver 实例，
    以 InMemorySaver 证明"传入即用、不作替换"。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    graph = build_agent_graph(saver)
    assert graph.checkpointer is saver
