/**
 * 设置面板：「设置」Tab 内容，提供主题切换、快捷键说明与关于信息。
 */
interface SettingsPanelProps {
  /** 当前主题 */
  theme: "dark" | "light";
  /** 切换主题 */
  onToggleTheme: () => void;
}

export function SettingsPanel({ theme, onToggleTheme }: SettingsPanelProps) {
  return (
    <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-400">外观</p>
        <button
          type="button"
          onClick={onToggleTheme}
          className="flex w-full cursor-pointer items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          <span>{theme === "dark" ? "🌙 暗色主题" : "☀️ 亮色主题"}</span>
          <span className="text-xs text-gray-400">{theme === "dark" ? "已启用" : "已启用"}</span>
        </button>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-400">快捷键</p>
        <ul className="space-y-1.5 rounded-lg border border-gray-200 bg-white p-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
          <li className="flex justify-between"><span>发送消息</span><kbd className="rounded bg-gray-100 px-1.5 text-xs dark:bg-gray-700">Ctrl + Enter</kbd></li>
          <li className="flex justify-between"><span>换行</span><kbd className="rounded bg-gray-100 px-1.5 text-xs dark:bg-gray-700">Shift + Enter</kbd></li>
          <li className="flex justify-between"><span>新建对话</span><kbd className="rounded bg-gray-100 px-1.5 text-xs dark:bg-gray-700">侧栏新建按钮</kbd></li>
        </ul>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-400">关于</p>
        <div className="rounded-lg border border-gray-200 bg-white p-3 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
          <p className="font-medium text-gray-800 dark:text-gray-100">CodeWise v1.0.0</p>
          <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
            自纠正 Python 代码 Agent：ReAct + Self-Reflection + Tool-Augmented
            三大范式，LangGraph 图编排，自动验证与反思优化闭环。
          </p>
        </div>
      </div>
    </div>
  );
}
