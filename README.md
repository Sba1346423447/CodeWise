# CodeWise 智码

基于 **LangGraph + FastAPI + React** 构建的自纠正式 AI 编程助手，采用 **Agent 自主工作流**，LLM 借助 ReAct 循环自主决策代码生成、验证执行、批判反思与迭代优化，全程 SSE 流式输出，提供对话式 Web 前端与 Docker 一键部署。

---

## 核心特性

- **Agent 自主工作流**：LangGraph 状态机编排「生成 → 验证 → 反思 → 优化 → 交付」完整闭环，LLM 自主决策每一步，无需人工介入
- **ReAct 循环**：Thought → Action → Observation 循环，LLM 判断产出代码还是调用工具，观察结果驱动下一步决策
- **Self-Reflection 四维批判**：从正确性 / 性能 / 可读性 / 类型安全四个维度审查代码，按意见重写后重新验证，形成自纠正闭环
- **客观验证闭环**：验证结果由真实运行退出码决定，不依赖 LLM 自评；附历史最优快照回退、冒烟测试兜底、循环次数护栏
- **Tool-Augmented 工具扩展**：隔离沙箱代码执行、自动化验证、静态检查、联网检索、文件编辑，五类工具可插拔扩展
- **代码库感知（repo-map）**：扫描项目结构生成类/函数摘要注入 LLM，让 Agent 基于已有代码库工作（对标 Aider 核心设计）
- **三层记忆架构**：会话内对话记忆（多轮演进）、单任务反思记录、跨会话长期经验库（ChromaDB 向量检索复用）
- **SSE 流式输出**：正文逐字 + 代码逐行打字机效果，思考过程实时可视化
- **Web 前端**：React + TypeScript 单页应用，支持会话管理、Markdown 渲染、思考过程折叠展示、深色主题
- **Docker 部署**：四服务（backend + frontend + chromadb + nginx）一键容器化启动

---

## 技术栈

| 层面 | 技术 |
|------|------|
| 语言 | Python 3.12+ / TypeScript 5 |
| Agent 编排 | LangGraph 1.x（StateGraph 状态机） |
| LLM | OpenAI 协议兼容（火山方舟 / 通义千问 / DeepSeek 等） |
| 向量数据库 | ChromaDB（独立服务） |
| 数据持久化 | SQLite（aiosqlite，会话 / 步骤 / 消息） |
| Web API | FastAPI + Uvicorn + SSE |
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| 部署 | Docker / Docker Compose |

---

## 项目结构

```
databox/
├── config/                     # 提示词 / 主配置（非代码人员可调优）
│   ├── prompts.yaml            # 各阶段提示词模板（ReAct / 反思 / 优化）
│   └── settings.yaml           # 模型名、反思轮次、沙箱、代码库感知 repo_map
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # Agent SSE 接口 / 会话管理 / 导出
│   │   ├── core/
│   │   │   ├── orchestrator.py # 编排器：协调 Graph / Tools / Memory / LLM
│   │   │   ├── repo_map.py     # 代码库感知：AST 扫描生成项目结构摘要
│   │   │   ├── graph/          # 图编排：state / nodes / edges / builder
│   │   │   ├── prompts/        # ReAct / Reflection / Refine 提示词
│   │   │   └── tools/          # 代码执行 / 验证 / 静态检查 / 联网检索 / 文件编辑
│   │   ├── llm/                # OpenAI 兼容客户端封装
│   │   ├── memory/             # 对话记忆 / 反思记忆 / 经验库
│   │   ├── models/             # SQLite 会话 / 步骤 / 消息模型
│   │   └── utils/              # 沙箱 / 日志 / SSE 工具
│   ├── tests/                  # 后端测试套件
│   ├── requirements.txt
│   ├── .env.example            # 后端环境变量模板
│   └── Dockerfile
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── components/         # 消息列表 / 代码查看器 / 会话历史等
│   │   ├── hooks/              # SSE 流式 / 会话历史 Hooks
│   │   ├── types/              # TypeScript 类型定义
│   │   └── utils/              # API 封装
│   ├── package.json
│   ├── nginx.conf              # 生产环境 Nginx 配置
│   └── Dockerfile
├── docker-compose.yml          # backend + frontend + chromadb + nginx
├── Makefile                    # install / dev / test / build / up / down
└── .env.example                # 根目录环境变量模板
```

---

## 快速开始

### 环境要求

- Python 3.12+（本地开发）
- Node 20+（本地开发）
- 可用的大模型 API Key（兼容 OpenAI 协议）

### 1. 克隆项目

```bash
git clone https://github.com/yourname/CodeWise.git
cd CodeWise
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：
- 填入大模型 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`
- 按需调整端口映射（`NGINX_PORT` / `CHROMA_PORT`）与日志级别

### 3. 安装依赖

```bash
cd backend
pip install -r requirements.txt

cd ../frontend
npm install
```

### 4. 可选：启动 ChromaDB（启用长期经验库）

```bash
docker run -d -p 8001:8000 chromadb/chroma
```

未启动时经验库自动降级为空库，不影响对话与代码生成主流程。

---

## 启动方式

### 方式一：本地开发（推荐调试）

```bash
# 启动后端（热重载，端口 8000）
cd backend
uvicorn app.main:app --reload

# 另开终端启动前端（端口 5173，Vite 已代理 /api → 8000）
cd frontend
npm run dev
```

启动后访问：
- 前端页面：http://localhost:5173
- API 文档：http://localhost:8000/docs

### 方式二：Docker 一键部署

```bash
cp .env.example .env
docker compose up -d --build
```

启动后访问 **http://localhost:8080**（nginx 网关，静态页面 + `/api` 反向代理 + SSE）。

**四服务架构：**

| 服务 | 镜像来源 | 对外端口 | 职责 |
|------|----------|----------|------|
| `nginx` | `nginx:1.27-alpine` | `8080` | 网关：静态页面 + `/api` 反向代理 + SSE 透传 |
| `frontend` | Node 20 多阶段构建 | — | Vite 构建产物，由网关托管 |
| `backend` | `python:3.12-slim` | — | FastAPI + LangGraph 核心逻辑 |
| `chromadb` | `chromadb/chroma:latest` | `8001` | 长期经验库向量存储 |

**常用运维命令：**

```bash
docker compose up -d --build   # 构建并启动
docker compose ps              # 查看服务状态
docker compose logs -f backend # 跟踪后端日志
docker compose down            # 停止并移除容器（保留卷数据）
```

**API 接口：**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/agent` | POST | Agent 对话（SSE 流式推送推理过程） |
| `/api/sessions` | GET / POST | 会话列表查询 / 创建 |
| `/api/sessions/{id}` | GET / DELETE / PATCH | 会话详情 / 删除 / 重命名 |
| `/api/export/{id}` | GET | 导出会话结果（Markdown / JSON） |

请求示例：

```json
{
  "task_desc": "写一个带缓存与 TTL 的 LRU 类",
  "session_id": "optional-existing-session-id"
}
```

---

## 工作流概览

```
用户需求
   │
   ▼
┌─ react_node ──工具调用──► tool_node ──►（ReAct 循环）
│        │
│        产出代码
│        ▼
│   test_gen_node（生成验证用例，复用/按需重生成）
│        ▼
│   test_node（真实运行，客观判定是否通过）
│        ▼
│   reflect_node（四维批判：正确性 / 性能 / 可读性 / 类型安全）
│        ▼
│   refine_node（按意见重写，改坏则回退最优快照）
│        ▼
└─ finalize_node（交付：总结 + 最终代码 + 结论）
```

---

## 项目亮点

- **自纠正闭环**：不是「AI 能写代码」，而是「AI 写完代码后能自己验证、自己修好」——验证结果由真实运行退出码决定，杜绝「AI 自评自夸」
- **代码库感知**：repo-map 扫描项目结构注入 LLM，Agent 能基于已有代码库工作，而非生成孤立代码（对标 Aider）
- **文件编辑能力**：内置 file_editor 工具，Agent 可真实读写项目文件，修改落地后仍走自纠闭环验证
- **图编排架构**：LangGraph StateGraph 显式建模节点与条件路由，状态流转清晰可追踪，并发安全
- **三层记忆体系**：会话内对话记忆、单任务反思记录、跨会话向量经验库，越用越聪明
- **配置与代码解耦**：提示词模板 / 模型参数统一 YAML 管理，非代码人员可调优
- **客观验证闭环**：历史最优快照回退、冒烟测试兜底、循环次数护栏，保证收敛不失控
- **全栈工程化**：前后端接口契约严格对齐、Docker 四服务编排、Makefile 快捷命令、结构化日志

---

## License

MIT
