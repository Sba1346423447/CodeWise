/**
 * 加载动画：Agent 执行中的等待状态指示（环形转圈 + 文案）。
 */

interface LoadingSpinnerProps {
  /** 展示文案，默认 "Agent 正在思考..." */
  text?: string;
}

export function LoadingSpinner({ text = "Agent 正在思考..." }: LoadingSpinnerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center justify-center gap-3 py-8"
    >
      {/* 转圈：使用 tailwind 内置 animate-spin + 品牌色阶 */}
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-brand-200 border-t-brand-600" />
      <p className="text-sm text-gray-500">{text}</p>
    </div>
  );
}
