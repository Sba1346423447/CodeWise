/**
 * 思考过程（对齐 WorkBuddy 规范）：默认折叠的浅灰小标题条，
 * 放在 AI 回复最开头，展开后显示工具调用与推理步骤，不占用主视觉空间。
 */
import { useState } from "react";
import type { ThinkingItem } from "../types/agent";
import { DiffViewer } from "./DiffViewer";

interface ThinkingBlockProps {
  items: ThinkingItem[];
  /** Agent 仍执行中：标题旁展示脉冲状态点 */
  loading?: boolean;
}

/** 步骤类型 → 前缀符号与颜色点 */
const TYPE_DOTS: Record<ThinkingItem["type"], string> = {
  react: "bg-brand-500",
  tool: "bg-warning",
  reflect: "bg-danger",
  refine: "bg-success",
};

export function ThinkingBlock({ items, loading = false }: ThinkingBlockProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden rounded-lg">
      {/* 浅灰小标题条：默认折叠，轻量不喧宾夺主 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-2 rounded-lg bg-gray-100 px-3 py-1.5 text-left text-xs text-gray-500 transition-colors hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-700"
      >
        <span className={`text-[10px] text-gray-400 transition-transform duration-150 ${open ? "rotate-90" : ""}`}>
          ▶
        </span>
        <span className="font-medium">思考过程</span>
        {loading && (
          <span className="ml-0.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
        )}
        <span className="ml-auto text-gray-400">{items.length} 步</span>
      </button>

      {/* 展开区：工具调用与推理步骤 */}
      {open && (
        <ul className="mt-1 space-y-1.5 rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-900/60">
          {items.map((item, index) => (
            <li key={index} className="flex items-start gap-2 text-xs">
              <span className={`mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${TYPE_DOTS[item.type] ?? TYPE_DOTS.react}`} />
              <div className="min-w-0">
                <span className="text-gray-600 dark:text-gray-300">{item.label}</span>
                {item.detail && (
                  <p className="mt-0.5 line-clamp-2 whitespace-pre-wrap break-all text-gray-400 dark:text-gray-500">
                    {item.detail}
                  </p>
                )}
                {/* 代码改动对比：仅 refine 步骤携带 diff */}
                {item.diff && <DiffViewer before={item.diff.before} after={item.diff.after} />}
              </div>
            </li>
          ))}
          {loading && items.length === 0 && (
            <li className="flex items-center gap-2 py-0.5 text-xs text-gray-400">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
              Agent 正在处理...
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
