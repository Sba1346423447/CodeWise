"""FastAPI 应用入口：创建 app 实例、挂载路由、配置 CORS。

依赖：fastapi（应用框架）、python-dotenv（加载 backend/.env）；
生命周期内初始化日志与数据库，路由挂载 Agent / 会话 / 导出三组接口。
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 启动时显式加载 backend/.env（main.py 位于 backend/app/，需上溯一层到 backend/）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from . import __version__
from .api import agent, export, sessions
from .models.database import init_db
from .utils.logger import setup_logging


def _parse_cors_origins() -> List[str]:
    """解析 CORS_ORIGINS 环境变量（逗号分隔）；未配置时使用默认本地端口。"""
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化日志与数据库表结构。"""
    setup_logging()
    await init_db()
    yield


app = FastAPI(
    title="CodeWise API",
    description="CodeWise 智码 · 自纠正式 AI 编程助手后端服务",
    version=__version__,  # 复用包版本号，避免重复维护
    lifespan=lifespan,
)

# CORS：放行前端开发服务器与生产部署来源（由 CORS_ORIGINS 环境变量控制）
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由：Agent 对话 / 会话管理 / 导出
app.include_router(agent.router)
app.include_router(sessions.router)
app.include_router(export.router)


if __name__ == "__main__":
    import uvicorn

    # 本地调试入口；生产环境由 uvicorn 命令或 Docker 启动
    uvicorn.run(
        "app.main:app",
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=True,
    )
