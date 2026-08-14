/**
 * 根组件：对话式 AI 编程主界面（对齐 WorkBuddy 布局规范）。
 * 结构：
 * - 左侧可拖拽边栏：顶部 Logo/新建对话 + 功能 Tab（对话/项目/助理/设置）
 *   + 底部当前 Tab 内容
 * - 右侧主区：极窄标题栏（会话标题 + 刷新/重命名/导出）+ 消息流 + 底部输入区
 * 数据优先级：实时执行消息 > 历史会话回放 > 空态欢迎页。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { ChatInput, type ChatInputHandle } from "./components/ChatInput";
import type { ChatMessageData } from "./components/ChatMessage";
import { FileTree } from "./components/FileTree";
import { MessageList } from "./components/MessageList";
import { SessionHistory } from "./components/SessionHistory";
import { SettingsPanel } from "./components/SettingsPanel";
import { WelcomePage } from "./components/WelcomePage";
import { useAgentSSE } from "./hooks/useAgentSSE";
import { useSessionHistory } from "./hooks/useSessionHistory";
import type { SessionInfo } from "./types/agent";
import { exportSession, getSession, renameSession } from "./utils/api";

/** 历史会话详情（含多轮消息回放，messages 覆盖为前端渲染结构） */
type HistoryDetail = Omit<SessionInfo, "messages"> & { messages: ChatMessageData[] };

/** 主题偏好 key（localStorage） */
const THEME_KEY = "codewise-theme";

/** 侧栏宽度范围（可拖拽） */
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 400;
const SIDEBAR_DEFAULT = 240;

/** 功能 Tab 定义（图标 + 文字） */
const TABS = [
  { id: "chat", label: "对话", icon: "💬" },
  { id: "project", label: "项目", icon: "🗂" },
  { id: "assistant", label: "助理", icon: "🤖" },
  { id: "settings", label: "设置", icon: "⚙" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/** 初始化主题：localStorage 优先，其次跟随系统偏好 */
function initTheme(): "dark" | "light" {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function App() {
  const { sessions, loading, remove, refresh } = useSessionHistory();
  const { status, messages, error, run, reset, typingAssistantId } = useAgentSSE();

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [historyDetail, setHistoryDetail] = useState<HistoryDetail | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">(initTheme);
  const [activeTab, setActiveTab] = useState<TabId>("chat");
  const [sidebarWidth, setSidebarWidth] = useState(SIDEBAR_DEFAULT);
  // 输入框外部控制句柄：欢迎页快捷任务点击后通过 ref 填入提示词
  const chatInputRef = useRef<ChatInputHandle>(null);

  // 拖拽侧栏：鼠标按下记录起点，移动时更新宽度，抬起结束
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(SIDEBAR_DEFAULT);

  const startDrag = useCallback((e: React.MouseEvent) => {
    draggingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = sidebarWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [sidebarWidth]);

  // 拖拽中更新宽度（全局监听，保证指针移出侧栏仍生效）
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const delta = e.clientX - startXRef.current;
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidthRef.current + delta));
      setSidebarWidth(next);
    };
    const onUp = () => {
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // 主题切换：同步 <html class> 与 localStorage（所有 dark: 变体据此生效）
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  const running = status === "running";
  // 执行中或打字机进行中任一为真即锁定：流式指示/输入框禁用以 typingAssistantId 为准，
  // 保证 done 后（含异常/手动停止路径）不会因状态未归位而卡在禁用态或光标常闪。
  const isBusy = running || typingAssistantId !== null;

  // 展示数据：实时执行优先；否则优先历史回放，其次实时残留
  const displayMessages =
    !running && historyDetail && historyDetail.messages.length
      ? historyDetail.messages
      : messages;

  /** 当前会话标题：优先历史详情，其次会话列表，缺省"新对话" */
  const sessionTitle =
    historyDetail?.task_desc ||
    sessions.find((s) => s.session_id === activeSessionId)?.task_desc ||
    "";

  /** 发送消息：多轮复用 activeSessionId，新会话传 undefined；携带用户选择的模型 */
  const handleSend = async (content: string, model?: string) => {
    setHistoryDetail(null); // 进入实时，隐藏历史回放
    await run(content, activeSessionId ?? undefined, model);
    await refresh(); // 会话列表反映新建/更新
  };

  /** 新建对话：清空当前对话上下文，开始新一轮（新会话不继承旧记忆） */
  const handleNewChat = () => {
    reset();
    setActiveSessionId(null);
    setHistoryDetail(null);
  };

  /** 切换会话：加载该会话的多轮消息回放 */
  const handleSelect = async (sessionId: string) => {
    if (running) return; // 执行中禁止切换，避免上下文错乱
    setActiveSessionId(sessionId);
    try {
      const detail = await getSession(sessionId);
      const msgs: ChatMessageData[] = (detail.messages ?? []).map((m) => {
        // thinking 兼容两种存储：旧版行动摘要数组 / 新版打包字典（含结构化字段）。
        // 新版拆出 code / testsPassed / reflections 等，回放时复用实时结构化组件渲染，
        // 保证历史对话与实时对话展示效果一致。
        const packed = Array.isArray(m.thinking) ? undefined : m.thinking;
        // 优先使用打包字段中的 code；若落库缺失/旧数据无 code，回退从 content 的
        // ```python``` 代码块中提取，避免历史回放代码本体空白。
        const inlineMatch = m.content.match(/```python\s*\n([\s\S]*?)\n```/);
        const code = packed?.code?.trim() || inlineMatch?.[1]?.trim() || undefined;
        // 历史消息没有逐行打字机进度，回放时直接展示完整代码行数，避免 CodeFileViewer 因 lines=0 而空白。
        const codeLines = code ? code.split("\n").length : undefined;
        const content = code
          ? m.content.replace(/\n*```python\s*\n[\s\S]*?\n```\s*$/, "").trim()
          : m.content;
        return {
          id: m.message_id,
          role: m.role,
          content,
          thinking: packed?.steps ?? (Array.isArray(m.thinking) ? m.thinking : undefined),
          code,
          codeLines,
          // 问答消息无代码，不下发测试徽章/测试面板，避免渲染"✗ 未通过"误读
          testsPassed: code ? packed?.tests_passed : undefined,
          reflections: packed?.reflections,
          testResults: code ? packed?.test_results : undefined,
          model: packed?.model,
          elapsedMs: packed?.elapsed_ms,
        };
      });
      setHistoryDetail({ ...detail, messages: msgs });
    } catch {
      setHistoryDetail(null); // 加载失败回退空态
    }
  };

  /** 删除会话：成功后清空对应选中态 */
  const handleDelete = async (sessionId: string) => {
    try {
      await remove(sessionId);
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setHistoryDetail(null);
        reset();
      }
    } catch {
      // 删除失败信息已由 hook 记录，此处静默
    }
  };

  /** 重命名当前会话：弹窗输入新标题 */
  const handleRename = async () => {
    if (!activeSessionId) return;
    const title = window.prompt("重命名会话", sessionTitle);
    if (!title?.trim() || title.trim() === sessionTitle) return;
    try {
      await renameSession(activeSessionId, title.trim());
      await refresh();
      setHistoryDetail((prev) => (prev ? { ...prev, task_desc: title.trim() } : prev));
    } catch {
      // 重命名失败静默（错误已由 api 层抛出）
    }
  };

  /** 导出当前会话为 Markdown（下载文件） */
  const handleExport = async () => {
    if (!activeSessionId) return;
    try {
      const md = (await exportSession(activeSessionId, "markdown")) as string;
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `codewise-${activeSessionId.slice(0, 8)}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // 导出失败静默
    }
  };

  /** 刷新当前会话：重新拉取消息回放 */
  const handleRefresh = async () => {
    await refresh();
    if (activeSessionId) await handleSelect(activeSessionId);
  };

  /** 重新生成：找到该助手消息之前最近一条用户消息作为任务描述，复用 run 重发。
   * 简化实现——把该任务作为新任务重发（多轮上下文依赖 activeSessionId 由后端演进）。 */
  const handleRegenerate = useCallback(
    async (messageId: string) => {
      if (running) return;
      const idx = displayMessages.findIndex((m) => m.id === messageId);
      if (idx < 0) return;
      // 向前回溯到最近一条 user 消息，取其 content 作为任务描述
      let taskDesc = "";
      for (let i = idx - 1; i >= 0; i--) {
        if (displayMessages[i].role === "user") {
          taskDesc = displayMessages[i].content;
          break;
        }
      }
      if (!taskDesc.trim()) return;
      setHistoryDetail(null); // 进入实时，隐藏历史回放
      await run(taskDesc, activeSessionId ?? undefined);
      await refresh();
    },
    [running, displayMessages, run, activeSessionId, refresh],
  );

  return (
    // 顶层错误边界：任何子树渲染异常（如超长 Markdown 解析溢出）都不会白屏，
    // 而是显示"页面出错了"降级 UI 而非卸载整棵 React 树
    <ErrorBoundary>
    <div className="flex h-screen w-screen overflow-hidden bg-[#FAFAF9] text-gray-900 transition-colors dark:bg-[#0b0f19] dark:text-gray-100">
      {/* 左侧边栏：可拖拽宽度，上下分区 */}
      <aside
        className="flex shrink-0 flex-col border-r border-gray-200 bg-white transition-colors dark:border-gray-800 dark:bg-[#111827]"
        style={{ width: sidebarWidth }}
      >
        {/* 顶部：Logo + 新建对话 + 功能 Tab */}
        <div className="border-b border-gray-200 px-3 pb-2 pt-3 transition-colors dark:border-gray-800">
          <div className="mb-3 flex items-center gap-2 px-1">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white">
              ⌘
            </div>
            <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-100">CodeWise</h2>
          </div>
          <button
            type="button"
            onClick={handleNewChat}
            className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-sm font-medium text-brand-700 transition-colors hover:bg-brand-100 dark:border-brand-800 dark:bg-brand-900/30 dark:text-brand-300 dark:hover:bg-brand-900/50"
          >
            <span className="text-base leading-none">＋</span>
            新建对话
          </button>
          {/* 功能 Tab：对话 / 项目 / 助理 / 设置 */}
          <nav className="mt-3 grid grid-cols-4 gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex cursor-pointer flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[11px] transition-colors ${
                  activeTab === tab.id
                    ? "bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100"
                    : "text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800/60"
                }`}
              >
                <span className="text-sm leading-none">{tab.icon}</span>
                {tab.label}
              </button>
            ))}
          </nav>
        </div>

        {/* 底部：当前 Tab 内容 */}
        <div className="min-h-0 flex-1 overflow-hidden">
          {activeTab === "chat" && (
            <SessionHistory
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelect={handleSelect}
              onDelete={handleDelete}
              onNewChat={handleNewChat}
              loading={loading}
            />
          )}
          {activeTab === "project" && <FileTree />}
          {activeTab === "assistant" && (
            <div className="space-y-3 px-4 py-4 text-sm text-gray-500 dark:text-gray-400">
              <p className="font-medium text-gray-700 dark:text-gray-300">助理</p>
              <p className="text-xs leading-relaxed">
                CodeWise 助理基于 LangGraph 编排的 ReAct + Self-Reflection 范式，
                具备：代码生成、自动验证、批判反思、工具调用、跨会话经验记忆。
              </p>
              <p className="text-xs leading-relaxed">
                在对话中描述需求即可触发完整自纠正流程。
              </p>
            </div>
          )}
          {activeTab === "settings" && <SettingsPanel theme={theme} onToggleTheme={toggleTheme} />}
        </div>
      </aside>

      {/* 拖拽手柄 */}
      <div
        onMouseDown={startDrag}
        className="w-1 shrink-0 cursor-col-resize bg-transparent transition-colors hover:bg-brand-300 dark:hover:bg-brand-700"
        aria-hidden="true"
      />

      {/* 主区：极窄标题栏 + 消息流 + 底部输入 */}
      <main className="flex min-w-0 flex-1 flex-col">
        {/* 极窄标题栏：会话标题 + 刷新/重命名/导出 */}
        <header className="flex h-11 shrink-0 items-center gap-1 border-b border-gray-200 bg-white px-3 transition-colors dark:border-gray-800 dark:bg-[#111827]">
          <h2 className="min-w-0 flex-1 truncate px-1 text-sm font-medium text-gray-800 dark:text-gray-100">
            {sessionTitle || "新对话"}
          </h2>

          {/* 会话操作图标按钮（hover 显示提示） */}
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={handleRefresh}
              title="刷新"
              aria-label="刷新"
              className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
            </button>
            <button
              type="button"
              onClick={handleRename}
              disabled={!activeSessionId}
              title="重命名"
              aria-label="重命名"
              className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>
            </button>
            <button
              type="button"
              onClick={handleExport}
              disabled={!activeSessionId}
              title="导出 Markdown"
              aria-label="导出"
              className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m7 10 5 5 5-5" /><path d="M12 15V3" /></svg>
            </button>
          </div>
        </header>

        {/* 消息流：main 为 flex-col 容器，MessageList 直接 flex-1 撑满可滚动。
            细粒度错误边界：单条超长消息渲染异常仅降级该消息区域，不拖垮整个页面。
            空会话（消息列表为空）渲染欢迎页：欢迎语 + 快捷任务，点击模板填入输入框 */}
        {displayMessages.length === 0 ? (
          <WelcomePage onPick={(prompt) => chatInputRef.current?.setValue(prompt)} />
        ) : (
          <ErrorBoundary
            fallback={
              <div className="flex-1 p-6 text-center text-sm text-gray-400">
                部分消息渲染失败，已被错误边界隔离。可刷新或新建对话继续使用。
              </div>
            }
          >
            <MessageList
              messages={displayMessages}
              typingAssistantId={typingAssistantId}
              onRegenerate={handleRegenerate}
            />
          </ErrorBoundary>
        )}

        {error && (
          <p className="px-6 pb-1 text-center text-sm text-danger">{error}</p>
        )}
        <ChatInput ref={chatInputRef} onSend={handleSend} disabled={isBusy} />
      </main>
    </div>
    </ErrorBoundary>
  );
}
