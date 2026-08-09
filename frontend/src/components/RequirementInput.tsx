/**
 * 需求输入框：用户输入任务描述，提交触发 Agent 执行。
 */

import { useState } from "react";

interface RequirementInputProps {
  /** 提交任务描述（触发 Agent 执行） */
  onSubmit: (taskDesc: string) => void;
  /** Agent 执行中：禁用输入与提交 */
  disabled?: boolean;
}

export function RequirementInput({ onSubmit, disabled = false }: RequirementInputProps) {
  const [value, setValue] = useState("");

  const handleSubmit = () => {
    const taskDesc = value.trim();
    if (!taskDesc || disabled) return;
    onSubmit(taskDesc);
    setValue(""); // 提交成功后清空，等待下一次需求
  };

  return (
    <div className="flex w-full items-end gap-2 border-t border-gray-200 bg-white p-4">
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          // Enter 提交，Shift+Enter 换行
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
          }
        }}
        placeholder="描述你想生成的 Python 代码需求，例如：写一个计算斐波那契数列的函数..."
        disabled={disabled}
        rows={2}
        className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-gray-100"
      />
      <button
        type="button"
        onClick={handleSubmit}
        disabled={disabled || !value.trim()}
        className="rounded-lg bg-brand-600 px-4 py-2 text-sm text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-300"
      >
        提交
      </button>
    </div>
  );
}
