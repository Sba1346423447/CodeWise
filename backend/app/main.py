"""FastAPI 应用入口：创建 app 实例、挂载路由、配置 CORS / 鉴权 / 限流。

依赖：fastapi（应用框架）、python-dotenv（加载 backend/.env）；
生命周期内初始化日志与数据库，路由挂载 Agent / 会话 / 导出三组接口
（统一经 api.deps.verify_api_key 鉴权）；RateLimitMiddleware 对
POST /api/agent 按客户端 IP 做滑动窗口限流，防 LLM 成本失控。
"""

import os
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# 启动时显式加载 backend/.env（main.py 位于 backend/app/，需上溯一层到 backend/）
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from . import __version__  # noqa: E402  # .env 必须先于 app 模块导入加载
from .api import agent, deps, export, sessions  # noqa: E402
from .models.database import init_db  # noqa: E402
from .utils.logger import get_audit_logger, setup_logging  # noqa: E402


def _parse_cors_origins() -> list[str]:
    """解析 CORS_ORIGINS 环境变量（逗号分隔）；未配置时使用默认本地端口。"""
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 的滑动窗口限流中间件（内存实现，单实例部署适用）。

    仅限流 POST /api/agent（烧 LLM token 的接口）；SSE 长连接在请求进入时
    计数一次，流式期间不重复计数，长任务不受影响。
    """

    def __init__(self, app, times: int = 10, window: float = 60.0) -> None:
        super().__init__(app)
        self.times = times
        self.window = window
        # IP → 窗口内请求时间戳队列（滑动窗口实现）
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/agent":
            now = time.monotonic()
            hits = self._hits[request.client.host if request.client else "unknown"]
            # 淘汰窗口外的旧时间戳
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.times:
                retry_after = max(0.0, self.window - (now - hits[0]))
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"请求过于频繁，请 {retry_after:.0f} 秒后重试"},
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
            hits.append(now)
        return await call_next(request)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """审计日志中间件：记录每次 API 请求的用户/时间/动作/结果。

    - 用户：客户端 IP（单用户部署形态，以来源 IP 为主体标识）
    - 动作：HTTP 方法 + 路径
    - 结果：响应状态码 + 耗时（SSE 接口计到响应头返回时刻）
    审计事件经 get_audit_logger() 独立写入 logs/audit_*.log；/health 探活不记录。
    """

    _audit = get_audit_logger()

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            self._audit.info(f"ip={ip} {request.method} {request.url.path} -> 500 异常")
            raise
        elapsed_ms = (time.monotonic() - start) * 1000
        self._audit.info(
            f"ip={ip} {request.method} {request.url.path} -> {response.status_code} {elapsed_ms:.0f}ms"
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化日志与数据库迁移（Alembic upgrade head，幂等）。"""
    setup_logging()
    init_db()
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

# 接口限流：仅 POST /api/agent（LLM 消耗接口），参数由环境变量控制
app.add_middleware(
    RateLimitMiddleware,
    times=int(os.getenv("RATE_LIMIT_TIMES", "10")),
    window=float(os.getenv("RATE_LIMIT_WINDOW", "60")),
)

# 审计日志：所有 API 请求记录 用户(IP)/时间/动作/结果，独立写入 audit_*.log
app.add_middleware(AuditLogMiddleware)

# 挂载 API 路由：Agent 对话 / 会话管理 / 导出（统一走 API Key 鉴权）
# /health 不挂依赖，供 Docker healthcheck 与负载均衡探活匿名访问
_auth = [Depends(deps.verify_api_key)]
app.include_router(agent.router, dependencies=_auth)
app.include_router(sessions.router, dependencies=_auth)
app.include_router(export.router, dependencies=_auth)


@app.get("/health")
async def health() -> dict:
    """健康检查接口，返回服务状态。"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    # 本地调试入口；生产环境由 uvicorn 命令或 Docker 启动
    uvicorn.run(
        "app.main:app",
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
        reload=True,
    )
