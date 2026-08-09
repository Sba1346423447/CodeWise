# ===== CodeWise 快捷命令 =====
.PHONY: install dev test build up down

# 安装前后端依赖
install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

# 本地开发：并行启动后端（热重载）与前端（Vite）
dev:
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
