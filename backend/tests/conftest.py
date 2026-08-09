"""pytest 全局配置：确保 backend 根目录进入 sys.path，测试可直接 import app.*。

同时将 ChromaDB 指向未监听端口，使 ExperienceStore 导入时立即降级为空库，
避免测试因外部服务连接等待而阻塞（见 experience_store 的容错设计）。
"""

import os
import sys
from pathlib import Path

# backend 根目录（conftest.py 位于 backend/tests/，上溯一层）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# 测试环境不依赖外部 ChromaDB 服务：指向未监听端口，让 ExperienceStore
# 在导入时立即连接失败并降级为空库（见 experience_store 的容错设计），
# 保证测试不因网络等待而阻塞。
os.environ.setdefault("CHROMA_HOST", "127.0.0.1")
os.environ.setdefault("CHROMA_PORT", "59999")
