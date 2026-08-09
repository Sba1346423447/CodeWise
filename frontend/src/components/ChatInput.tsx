/**
 * 底部输入区（对齐 WorkBuddy 规范）：
 * - 浅灰背景圆角容器，多行输入，Enter 发送 / Shift+Enter 换行
 * - 左侧：附件上传、代码插入等图标按钮
 * - 右侧：模型选择下拉 + 发送按钮
 * - 下方外部标注快捷键提示
 */
import { useState } from "react";

interface ChatInputProps {
  /** 发送消息（多轮对话场景下，同会话内可连续调用） */
  onSend: (content: string) => void;
  /** Agent 执行中：禁用输入与发送 */
  disabled?: boolean;
}

/** 可用模型列表（与后端 LLM 配置对齐；实际模型名以 backend/.env 的 LLM_MODEL 为准） */
const MODELS = ["deepseek-v4-flash", "deepseek-v3", "gpt-4o", "doubao-seed-2-1-turbo"];

export function ChatInput({ onSend, disabled = false }: ChatInputProps) {
  const [value, setValue] = useState("");
  const [model, setModel] = useState(MODELS[0]);

  const handleSend = () => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content);
    setValue("");
  };

  /** 图标按钮通用样式 */
  const iconBtnCls =
    "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-200/60 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300";

  return (
    <div className="w-full border-t border-gray-200 bg-white px-4 pb-3 pt-3 transition-colors dark:border-gray-800 dark:bg-[#111827]">
      <div className="mx-auto max-w-3xl">
        {/* 浅灰背景圆角输入容器 */}
        <div className={`flex items-end rounded-xl bg-gray-100 p-2 transition-colors dark:bg-gray-800 ${disabled ? "opacity-70" : ""}`}>
          {/* 左侧：附件上传 / 代码插入 */}
          <div className="flex shrink-0 items-center gap-0.5 self-center">
            <button type="button" className={iconBtnCls} title="上传附件" aria-label="上传附件">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
            </button>
            <button type="button" className={iconBtnCls} title="插入代码块" aria-label="插入代码块">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m16 18 6-6-6-6" /><path d="m8 6-6 6 6 6" /></svg>
            </button>
          </div>

          {/* 多行输入框 */}
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Enter 发送，Shift+Enter 换行
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="今天帮你做点什么？描述编程需求即可"
            disabled={disabled}
            rows={2}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] text-gray-800 placeholder:text-gray-400 focus:outline-none disabled:cursor-not-allowed dark:text-gray-100 dark:placeholder:text-gray-500"
          />

          {/* 右侧：模型选择下拉 + 发送按钮 */}
          <div className="flex shrink-0 items-center gap-1 self-center">
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={disabled}
              title="选择模型"
              className="h-8 cursor-pointer rounded-lg bg-transparent px-1.5 text-xs text-gray-500 transition-colors hover:bg-gray-200/60 focus:outline-none disabled:cursor-not-allowed dark:text-gray-400 dark:hover:bg-gray-700"
            >
              {MODELS.map((m) => (
                <option key={m} value={m} className="bg-white dark:bg-gray-800">
                  {m}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleSend}
              disabled={disabled || !value.trim()}
              aria-label="发送"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white transition-colors hover:bg-brand-700 focus:outline-none disabled:cursor-not-allowed disabled:bg-gray-300 dark:disabled:bg-gray-600"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
            </button>
          </div>
        </div>

        {/* 下方外部快捷键提示 */}
        <p className="mt-1.5 text-center text-xs text-gray-400 dark:text-gray-500">
          Enter 发送 · Shift + Enter 换行
        </p>
      </div>
    </div>
  );
}
