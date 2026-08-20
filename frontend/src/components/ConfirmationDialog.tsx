/**
 * 安全审查确认对话框：Agent 工具调用被判定"需人工确认"（第四层 human-in-the-loop）
 * 时展示，列出待确认操作（工具名 / 参数预览 / 风险理由），用户批准后图线程恢复执行，
 * 拒绝则 Agent 收到失败 Observation 自行换方案。
 */

import type { PendingConfirmation } from "../types/agent";

interface ConfirmationDialogProps {
  /** 待确认信息（来自 confirmation_required 事件） */
  confirmation: PendingConfirmation;
  /** 用户响应：true 批准执行 / false 拒绝（Agent 将换方案） */
  onRespond: (approved: boolean) => void;
  /** 响应中禁用按钮（恢复请求进行中） */
  busy?: boolean;
}

/** 参数 JSON 预览最大长度（超长截断，防大段代码撑爆对话框） */
const ARGS_PREVIEW_MAX = 300;

/** 单个工具参数值的预览文本 */
function previewValue(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!text) return "";
  return text.length > ARGS_PREVIEW_MAX ? `${text.slice(0, ARGS_PREVIEW_MAX)}…（已截断）` : text;
}

export function ConfirmationDialog({ confirmation, onRespond, busy }: ConfirmationDialogProps) {
  return (
    <div className="mx-4 mb-3 rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm transition-colors dark:border-amber-700/60 dark:bg-amber-950/40">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-base leading-none">🛡</span>
        <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-300">
          安全审查：以下操作需要你的确认
        </h3>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-amber-700 dark:text-amber-400/90">
        风险分类器判定该操作可疑但可能有正当理由，已挂起 Agent 执行。批准后将恢复执行；
        拒绝后 Agent 会收到失败反馈并尝试其他方案。
      </p>

      <div className="space-y-2">
        {confirmation.tools.map((tool, i) => (
          <div
            key={`${tool.tool}-${i}`}
            className="rounded-lg border border-amber-200 bg-white/70 p-3 transition-colors dark:border-amber-800/60 dark:bg-gray-900/60"
          >
            <div className="mb-1 flex items-center gap-2">
              <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[11px] font-medium text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                {tool.tool}
              </span>
            </div>
            <p className="mb-1.5 text-xs text-gray-600 dark:text-gray-400">
              <span className="font-medium text-gray-700 dark:text-gray-300">风险理由：</span>
              {tool.reason || "（未提供）"}
            </p>
            {Object.keys(tool.args).length > 0 && (
              <pre className="max-h-40 overflow-auto rounded-md bg-gray-100 p-2 font-mono text-[11px] leading-relaxed text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                {Object.entries(tool.args)
                  .map(([k, v]) => `${k}: ${previewValue(v)}`)
                  .join("\n")}
              </pre>
            )}
          </div>
        ))}
      </div>

      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => onRespond(false)}
          className="cursor-pointer rounded-lg border border-gray-300 bg-white px-4 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          拒绝
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onRespond(true)}
          className="cursor-pointer rounded-lg bg-amber-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          批准执行
        </button>
      </div>
    </div>
  );
}
