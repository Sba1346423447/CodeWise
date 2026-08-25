# ===== 后端 Docker 镜像构建 =====
FROM python:3.12-slim

WORKDIR /app

# 系统依赖：chromadb / onnxruntime 运行时需要（缺失会报 libgomp 相关错误）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先安装依赖，利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码（backend/ 下全部）
COPY . .

# 非 root 运行：创建低权限用户并接管日志目录（卷挂载点，首建卷继承此属主）
RUN useradd --create-home --uid 1000 codewise \
    && mkdir -p /app/logs \
    && chown -R codewise:codewise /app/logs
USER codewise

# FastAPI 服务端口
EXPOSE 8000

# 容器健康检查：/health 无需鉴权（main.py 中未挂鉴权依赖）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令：uvicorn app.main:app（挂载于 backend 工作目录）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
