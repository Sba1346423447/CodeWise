"""API 鉴权依赖：X-API-Key 请求头校验（单用户形态，预留升级接口）。

设计：
- API_KEY 环境变量未配置时放行（本地单机开发模式，启动日志提示）；
- 配置后所有 /api/* 业务路由要求请求头 X-API-Key 与之相等，否则 401；
- /health 健康检查不经过本依赖（供 Docker healthcheck 与 LB 探活）。
"""

import os

from fastapi import Header, HTTPException

from ..utils.logger import get_logger

logger = get_logger("api.auth")

_warned_dev_mode = False


def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """校验请求头 X-API-Key；API_KEY 未配置时放行（本地开发模式）。"""
    global _warned_dev_mode
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        if not _warned_dev_mode:
            logger.warning("API_KEY 未配置，接口鉴权已关闭（本地开发模式）")
            _warned_dev_mode = True
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="无效的 API Key")
