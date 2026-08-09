/**
 * 最终结果：展示最终代码 + 测试通过状态 + 反思摘要。
 */

import type { AgentResult } from "../types/agent";
import { CodeViewer } from "./CodeViewer";

interface FinalResultProps {
  /** Agent 执行结果（done 事件载荷） */
  result: AgentResult | null;
}

export function FinalResult({ result }: FinalResultProps) {
  // 空态：尚未完成任何任务
  if (!result) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-6 text-center text-sm text-gray-400">
        Agent 完成后将在此展示最终代码与测试结论。
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 测试状态 + 反思摘要 */}
      <div className="flex items-center gap-3">
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium ${
            result.tests_passed
              ? "bg-success/10 text-success"
              : "bg-danger/10 text-danger"
          }`}
        >
          {result.tests_passed ? "✓ 验证通过" : "✗ 验证未通过"}
        </span>
        <span className="text-xs text-gray-500">
          反思轮次：{result.reflection_count}
        </span>
        <span className="truncate text-xs text-gray-400" title={result.task_desc}>
          任务：{result.task_desc}
        </span>
      </div>

      {/* 最终代码 */}
      <div>
        <h3 className="mb-2 text-sm font-medium text-gray-700">最终代码</h3>
        <CodeViewer code={result.final_code} />
      </div>
    </div>
  );
}
