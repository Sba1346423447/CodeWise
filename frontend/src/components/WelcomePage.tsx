/**
 * 中栏欢迎页：空会话（消息列表为空）时展示欢迎语 + 快捷任务区。
 * 快捷任务均为编程相关模板：点击后将提示词填入底部输入框（由 App 通过 onPick 接线）。
 */
interface QuickTask {
  icon: string;
  title: string;
  desc: string;
  prompt: string;
}

/** 编程相关快捷任务模板（点击填入输入框，用户可再编辑后发送） */
const QUICK_TASKS: QuickTask[] = [
  {
    icon: "⚙️",
    title: "写一个 Python 函数",
    desc: "例如：实现一个带 TTL 的 LRU 缓存",
    prompt: "写一个 Python 函数：实现一个带 TTL 与容量上限的 LRU 缓存类。",
  },
  {
    icon: "🧪",
    title: "写单元测试",
    desc: "为指定代码生成 pytest 测试用例",
    prompt: "为下面的 Python 代码生成一组 pytest 测试用例：覆盖正常输入、边界条件、异常分支。",
  },
  {
    icon: "🔁",
    title: "代码重构",
    desc: "优化现有实现的性能与可读性",
    prompt: "请重构以下代码：优化性能与可读性，保持对外行为不变。",
  },
  {
    icon: "📖",
    title: "解释代码",
    desc: "分析一段代码的意图与实现思路",
    prompt: "请解释下面这段代码：它的意图是什么、关键实现思路是什么。",
  },
  {
    icon: "🐛",
    title: "修复 Bug",
    desc: "根据报错信息定位并修复问题",
    prompt: "请根据下面的报错信息定位问题根因，并给出修复后的完整代码。",
  },
];

interface WelcomePageProps {
  /** 点击快捷任务模板：把模板提示词填入底部输入框 */
  onPick: (prompt: string) => void;
}

export function WelcomePage({ onPick }: WelcomePageProps) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col items-center px-6 py-16 text-center">
        {/* Logo：品牌渐变徽章 */}
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-2xl font-bold text-white shadow-lg shadow-brand-200">
          ⌘
        </div>
        <h1 className="mt-5 text-2xl font-semibold text-gray-900 dark:text-gray-100">
          今天帮你写点什么？
        </h1>
        <p className="mt-2 max-w-md text-sm leading-relaxed text-gray-500 dark:text-gray-400">
          描述你的编程需求，我会生成 Python 代码、自动验证，
          并通过自我批判与优化持续完善直到可用。
        </p>

        {/* 快捷任务区：编程相关模板，点击填入输入框 */}
        <div className="mt-8 grid w-full max-w-xl gap-2 text-left">
          {QUICK_TASKS.map((task) => (
            <button
              key={task.title}
              type="button"
              onClick={() => onPick(task.prompt)}
              className="flex cursor-pointer items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-left shadow-sm transition-colors hover:border-brand-300 hover:bg-brand-50/40 dark:border-gray-700 dark:bg-gray-900 dark:hover:border-brand-700 dark:hover:bg-brand-900/20"
            >
              <span className="text-base">{task.icon}</span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-gray-700 dark:text-gray-200">
                  {task.title}
                </span>
                <span className="block truncate text-xs text-gray-400 dark:text-gray-500">
                  {task.desc}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
