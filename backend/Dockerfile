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

# FastAPI 服务端口
EXPOSE 8000

# 启动命令：uvicorn app.main:app（挂载于 backend 工作目录）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
