/**
 * 执行时间线：按时间顺序流式渲染所有步骤卡片。
 */

import type { AgentStep } from "../types/agent";
import { LoadingSpinner } from "./LoadingSpinner";
import { StepCard } from "./StepCard";

interface ExecutionTimelineProps {
  /** 步骤列表（SSE 流式追加，组件随数组增长自动渲染） */
  steps: AgentStep[];
  /** Agent 执行中：列表底部展示等待指示 */
  loading?: boolean;
}

export function ExecutionTimeline({ steps, loading = false }: ExecutionTimelineProps) {
  return (
    <div className="space-y-3">
      {/* 空状态：尚未执行任何任务 */}
      {steps.length === 0 && !loading && (
        <p className="py-8 text-center text-sm text-gray-400">
          暂无执行步骤，输入需求后 Agent 的推理过程将在这里实时展示。
        </p>
      )}

      {/* 步骤卡片：按数组顺序即时间顺序渲染 */}
      {steps.map((step, index) => (
        <StepCard key={step.step_id} step={step} index={index} />
      ))}

      {/* 执行中：底部展示加载指示 */}
      {loading && <LoadingSpinner />}
    </div>
  );
}
