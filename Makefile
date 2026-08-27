# ===== CodeWise 快捷命令 =====
.PHONY: install dev chroma-up chroma-down test build up down

# 安装前后端依赖
install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

# 拉起本地 Chroma 服务（不用 Docker：本地 chromadb 进程，数据落 backend/chroma_data）。
# 幂等：端口 8000 已被占用说明已在运行，直接跳过
chroma-up:
	@if curl -s http://localhost:8000/api/v1/heartbeat >/dev/null 2>&1; then \
		echo "Chroma 已在运行 (localhost:8000)"; \
	else \
		echo "启动本地 Chroma (localhost:8000)..."; \
		mkdir -p backend/logs; \
		nohup chroma run --path backend/chroma_data --port 8000 > backend/logs/chroma.log 2>&1 & \
		echo "Chroma 启动中，日志：backend/logs/chroma.log"; \
	fi

# 停止本地 Chroma 进程
chroma-down:
	@-pkill -f "chroma run --path backend/chroma_data" 2>/dev/null || echo "无本地 Chroma 进程"

# 本地开发：先拉起 Chroma，再并行启动后端（热重载）与前端（Vite）
dev: chroma-up
	cd backend && uvicorn app.main:app --reload &
	cd frontend && npm run dev

# 运行后端测试
test:
	cd backend && pytest

# 构建 Docker 镜像（四服务）
build:
	docker compose build

# 启动全部服务（后台）
up:
	docker compose up -d

# 停止并移除容器
down:
	docker compose down
