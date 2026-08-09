/**
 * API 工具：统一封装 fetch 请求、错误处理与 base URL 配置。
 * 开发环境由 vite 代理转发 /api → 后端；生产环境由 nginx 反向代理转发，
 * 故 base URL 恒为相对路径，无需硬编码域名。
 */

import type { AgentStep, SessionInfo, StoredMessage } from "../types/agent";

// 如未来需要指向独立后端域名，可改为通过构建时注入（vite define / 环境变量）替换
const BASE_URL = "";

/** 统一 API 异常：携带 HTTP 状态码与后端返回的 detail 信息 */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 统一 JSON 请求封装：非 2xx 抛 ApiError，成功返回解析后的数据 */
async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!response.ok) {
    // 后端 FastAPI 错误结构：{"detail": "..."}
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.detail ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

/** 创建会话（仅保存任务描述；Agent 执行由 SSE 接口触发） */
export function createSession(taskDesc: string): Promise<SessionInfo> {
  return apiFetch<SessionInfo>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ task_desc: taskDesc }),
  });
}

/** 列出全部历史会话（按创建时间倒序） */
export function listSessions(): Promise<SessionInfo[]> {
  return apiFetch<SessionInfo[]>("/api/sessions");
}

/** 查询会话详情（附带执行步骤时间线与多轮对话消息） */
export function getSession(
  sessionId: string,
): Promise<SessionInfo & { steps: AgentStep[]; messages: StoredMessage[] }> {
  return apiFetch<SessionInfo & { steps: AgentStep[]; messages: StoredMessage[] }>(
    `/api/sessions/${sessionId}`,
  );
}

/** 删除会话及其关联步骤 */
export function deleteSession(sessionId: string): Promise<{ deleted: string }> {
  return apiFetch<{ deleted: string }>(`/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** 重命名会话（更新任务描述） */
export function renameSession(
  sessionId: string,
  taskDesc: string,
): Promise<SessionInfo> {
  return apiFetch<SessionInfo>(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ task_desc: taskDesc }),
  });
}

/** 导出会话结果：markdown 返回文本，json 返回结构化对象 */
export async function exportSession(
  sessionId: string,
  format: "markdown" | "json" = "markdown",
): Promise<string | Record<string, unknown>> {
  const response = await fetch(`${BASE_URL}/api/export/${sessionId}?format=${format}`);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.detail ?? `导出失败（${response.status}）`);
  }
  return format === "json" ? response.json() : response.text();
}
