/**
 * 测试结果面板：展示最终 pytest 统计（通过/失败/错误）与可展开的失败输出详情。
 * 数据来源：AgentResult.test_results（后端 test_node 客观结果，非 LLM 自评）。
 */
import { memo, useState } from "react";
import type { TestResults } from "../types/agent";

interface TestResultsPanelProps {
  /** 测试统计与输出（可空：无测试运行时隐藏面板） */
  results?: TestResults;
  /** 测试是否通过（控制整体状态色） */
  testsPassed?: boolean;
}

export const TestResultsPanel = memo(function TestResultsPanel({
  results,
  testsPassed,
}: TestResultsPanelProps) {
  const [open, setOpen] = useState(false);
  const total = (results?.passed ?? 0) + (results?.failed ?? 0) + (results?.errors ?? 0);
  const output = results?.output?.trim();

  // 无测试运行记录时隐藏面板（不占用对话空间）
  if (!results || total === 0) return null;

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-gray-200 bg-gray-50/60 transition-colors dark:border-gray-700 dark:bg-gray-800/40">
      {/* 概要行：状态 + 统计 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <span
          className={`flex items-center gap-1.5 text-xs font-medium ${
            testsPassed ? "text-success dark:text-emerald-400" : "text-danger dark:text-rose-400"
          }`}
        >
          <span className="text-sm leading-none">{testsPassed ? "✓" : "✗"}</span>
          {testsPassed ? "验证通过" : "验证未通过"}
        </span>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          通过 <span className="font-medium text-emerald-600 dark:text-emerald-400">{results.passed}</span>
          <span className="mx-1 text-gray-300 dark:text-gray-600">·</span>
          失败 <span className="font-medium text-rose-600 dark:text-rose-400">{results.failed}</span>
          <span className="mx-1 text-gray-300 dark:text-gray-600">·</span>
          错误 <span className="font-medium text-amber-600 dark:text-amber-400">{results.errors}</span>
        </span>

        {/* 失败且有输出：提供展开入口 */}
        {!testsPassed && output && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-gray-500 transition-colors hover:bg-gray-200/60 dark:text-gray-400 dark:hover:bg-gray-700/60"
          >
            <span className={`text-[10px] transition-transform duration-200 ${open ? "rotate-90" : ""}`}>▶</span>
            {open ? "收起详情" : "查看详情"}
          </button>
        )}
      </div>

      {/* 失败详情：pytest 输出（traceback / 断言信息） */}
      {open && output && (
        <pre className="max-h-64 overflow-auto border-t border-gray-200 bg-[#0f172a] px-3 py-2 font-mono text-[12px] leading-relaxed text-gray-200 dark:border-gray-700">
          {output}
        </pre>
      )}
    </div>
  );
});
