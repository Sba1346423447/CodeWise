# ===== 阶段 1：Node 构建（Vite 打包前端产物） =====
FROM node:20-alpine AS build
WORKDIR /app

# 先拷贝依赖清单并安装，利用 Docker 层缓存（package.json 未变时复用依赖层）
COPY package.json ./
RUN npm install

# 拷贝源码后构建（tsc + vite build → dist/）
COPY . .
RUN npm run build

# ===== 阶段 2：Nginx 运行（静态服务 + API 反向代理） =====
FROM nginx:1.27-alpine

# 使用项目自定义配置（SPA 回退 + /api 代理 + SSE 缓冲关闭）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 拷贝阶段 1 的构建产物
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
