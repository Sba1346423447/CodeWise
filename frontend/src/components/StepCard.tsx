/**
 * 步骤卡片：展示单个 ReAct 步骤（Thought/Action/Observation 对应的节点产出）。
 */

import type { AgentStep, StepType } from "../types/agent";

/** 步骤类型徽章样式映射（品牌色系区分四类节点） */
const STEP_STYLES: Record<StepType, { label: string; badge: string }> = {
  react: { label: "ReAct 思考", badge: "bg-brand-100 text-brand-700" },
  tool: { label: "工具执行", badge: "bg-amber-100 text-amber-700" },
  reflect: { label: "批判反思", badge: "bg-rose-100 text-rose-700" },
  refine: { label: "代码优化", badge: "bg-emerald-100 text-emerald-700" },
};

/** 将 ISO 时间转为 HH:mm:ss */
function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}

/** 从节点更新 JSON 中提取可读内容（取最新一条消息的 content，失败回退原文） */
function extractContent(output: string): string {
  try {
    const update = JSON.parse(output) as { messages?: Array<{ content?: string }> };
    const messages = update.messages;
    if (Array.isArray(messages) && messages.length) {
      const last = messages[messages.length - 1];
      if (last?.content) return last.content;
    }
    return output;
  } catch {
    return output;
  }
}

interface StepCardProps {
  step: AgentStep;
  /** 步骤序号（用于时间线顺序展示） */
  index: number;
}

export function StepCard({ step, index }: StepCardProps) {
  const style = STEP_STYLES[step.step_type] ?? STEP_STYLES.react;

  return (
    <div className="step-card rounded-lg border border-gray-200 bg-white p-4">
      {/* 头部：序号 + 类型徽章 + 时间 */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">#{index + 1}</span>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${style.badge}`}>
            {style.label}
          </span>
        </div>
        <span className="text-xs text-gray-400">{formatTime(step.created_at)}</span>
      </div>

      {/* 内容：优先展示消息正文，否则展示原始 JSON 更新 */}
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-50 p-2 font-mono text-xs text-gray-700">
        {extractContent(step.output)}
      </pre>
    </div>
  );
}
