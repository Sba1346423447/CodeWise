/**
 * CodeWise 前端类型定义：与后端接口契约严格对齐（禁止私自变更字段）。
 * 对应后端：sse.py 事件名 / orchestrator 返回结构 / sessions、steps 表结构。
 */

/** Agent 执行步骤类型（与后端图四节点一一对应） */
export type StepType = "react" | "tool" | "reflect" | "refine";

/** 会话状态（与后端 session.py 的 STATUS_* 对齐） */
export type SessionStatus =
  | "running"
  | "completed"
  | "failed"
  | "stopped"
  | "awaiting_confirmation";

/** OpenAI 兼容消息（与后端 LLM messages 格式一致） */
export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
}

/** 工具调用（Function Calling，对应后端 llm/client.py 流式事件结构） */
export interface ToolCall {
  index: number;
  id: string;
  function: {
    name: string;
    arguments: string;
  };
}

/** Agent 运行状态快照（对应后端 AgentState 字段） */
export interface AgentState {
  task_desc: string;
  messages: Message[];
  reflection_count: number;
  critique: string;
  current_code: string;
  final_code: string;
  tests_passed: boolean;
}

/** 行动摘要条目（对应后端 orchestrator 提炼的 thinking 数组结构） */
export interface ThinkingItem {
  /** 步骤类型：react | tool | reflect | refine */
  type: "react" | "tool" | "reflect" | "refine";
  /** 简短标题，如"运行验证" */
  label: string;
  /** 详情说明（可空），如测试失败原因摘要 */
  detail?: string;
  /** 代码改动对比（仅 refine 步骤）：before 修改前 / after 修改后，供 Diff 视图展示 */
  diff?: { before: string; after: string };
}

/** 测试运行统计（对应后端 nodes.test_node 的 test_results 结构） */
export interface TestResults {
  passed: number;
  failed: number;
  errors: number;
  /** pytest 输出（截断到 2000 字符，含失败详情 traceback） */
  output?: string;
}

/** 安全审查待确认的单个工具操作（对应后端 confirm_node 的 interrupt value） */
export interface PendingTool {
  /** 工具名，如 file_editor / code_executor */
  tool: string;
  /** 工具参数（原始 JSON 反序列化后的对象） */
  args: Record<string, unknown>;
  /** 风险分类器给出的判定理由 */
  reason: string;
}

/** 安全审查挂起信息（confirmation_required 事件 / done 结果的 pending_confirmation） */
export interface PendingConfirmation {
  /** 挂起的图线程 ID：确认时回传后端恢复执行 */
  run_id: string;
  /** 待确认的工具操作列表 */
  tools: PendingTool[];
}

/** Agent 最终交付结果（对应后端 orchestrator._build_result 返回） */
export interface AgentResult {
  task_desc: string;
  final_code: string;
  final_message: string;
  messages: Message[];
  reflection_count: number;
  tests_passed: boolean;
  /** 最终测试运行详情（供前端测试结果面板展示） */
  test_results?: TestResults;
  /** 执行耗时（毫秒），供消息底部元信息展示 */
  elapsed_ms?: number;
  /** 使用的大模型名称，供消息底部元信息展示 */
  model?: string;
  /** 本轮行动摘要（供前端折叠展示，可空） */
  thinking?: ThinkingItem[];
  /** 用户手动停止：done 事件由停止收尾分支推送（前端停止时通常已断开，容错字段） */
  stopped?: boolean;
  /** 安全审查挂起信息：非空表示图在人工确认处暂停，未产出交付物 */
  pending_confirmation?: PendingConfirmation | null;
}

/** Agent 任务请求体（对应后端 agent.py 的 AgentRequest） */
export interface AgentRequest {
  /** 会话 ID：新会话首次可不带（后端新建并返回）；多轮必带 */
  session_id?: string;
  task_desc: string;
}

/** 单条执行步骤记录（步骤卡片 / 时间线数据源） */
export interface AgentStep {
  step_id: string;
  session_id: string;
  step_type: StepType;
  input: string;
  output: string;
  created_at: string;
}

/** 历史消息 thinking 打包结构（后端 agent.py 落库时写入，供回放还原结构化组件） */
export interface StoredThinking {
  /** 行动摘要列表（等同实时 thinking） */
  steps?: ThinkingItem[];
  /** 最终代码 */
  code?: string;
  /** 是否通过验证 */
  tests_passed?: boolean;
  /** 优化轮数 */
  reflections?: number;
  /** 验证结果统计 */
  test_results?: TestResults;
  /** 模型名 */
  model?: string;
  /** 耗时（毫秒） */
  elapsed_ms?: number;
}

/** 持久化消息记录（对应后端 messages 表，供历史会话回放） */
export interface StoredMessage {
  message_id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  /** 兼容两种存储：旧版行动摘要数组 / 新版打包字典 */
  thinking?: ThinkingItem[] | StoredThinking | null;
  created_at: string;
}

/** 会话信息（对应后端 sessions 表 + 可选步骤列表 + 可选消息回放） */
export interface SessionInfo {
  session_id: string;
  task_desc: string;
  status: SessionStatus;
  final_code: string | null;
  created_at: string;
  steps?: AgentStep[];
  messages?: StoredMessage[];
}

/** SSE 事件（判别联合，与后端 sse.py 的 7 个事件名严格对齐） */
export type SSEEvent =
  | { type: "agent_start"; data: { session_id: string; run_id: string } }
  | { type: "node"; data: { node: string; update: Record<string, unknown> } }
  | { type: "content"; data: { delta: string } }
  | { type: "tool_calls"; data: { delta: ToolCall[] } }
  | { type: "confirmation_required"; data: PendingConfirmation }
  | { type: "done"; data: AgentResult }
  | { type: "error"; data: { message: string } };
