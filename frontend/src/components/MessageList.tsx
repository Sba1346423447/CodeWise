/**
 * 消息列表容器：渲染全部对话消息，内容变化时平滑滚动到底部（流式跟随）。
 * 居中布局（最大宽度约 760px）形成阅读舒适区，替代原三栏 debug 布局。
 *
 * 每条 ChatMessage 外单独包一层 ErrorBoundary：单条消息渲染异常（如超长
 * Markdown 解析溢出、Prism.highlight 栈溢出等）仅降级该条，不影响整列表。
 */
import { useEffect, useRef } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { ChatMessage, type ChatMessageData } from "./ChatMessage";

interface MessageListProps {
  messages: ChatMessageData[];
  /** 正在流式生成的助手消息 id（精确到消息，不依赖全局 running） */
  typingAssistantId?: string | null;
}

export function MessageList({ messages, typingAssistantId = null }: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // 消息新增或流式增量更新时，仅在"接近底部"时吸附到底部：
  // 打字机逐字更新期间，用户主动上翻查看历史/长代码时不被强制拉回
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom) {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    }
  }, [messages]);

  return (
    // min-h-0：允许在 flex 容器内收缩到视口高度，overflow-y-auto 才能产生滚动条
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
        {messages.length === 0 && (
          <div className="flex flex-col items-center px-6 py-24 text-center">
            {/* Logo：品牌渐变徽章 */}
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-2xl font-bold text-white shadow-lg shadow-brand-200">
              ⌘
            </div>
            <h1 className="mt-5 text-2xl font-semibold text-gray-900 dark:text-gray-100">CodeWise</h1>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-gray-500 dark:text-gray-400">
              你好，我是你的 AI 编程助手。描述你的需求，我会生成
              Python 代码、自动验证，并通过优化持续完善直到可用。
            </p>
            {/* 示例提示：静态引导，帮助新用户快速理解能力 */}
            <div className="mt-8 grid w-full max-w-md gap-2 text-left">
              {[
                ["⚙️", "写一个带缓存与 TTL 的 LRU 类"],
                ["🧪", "用递归实现二分查找，并自动验证正确性"],
                ["🔁", "把斐波那契改写成迭代并对比性能"],
              ].map(([icon, text]) => (
                <div
                  key={text}
                  className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600 shadow-sm transition-colors dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
                >
                  <span className="text-base">{icon}</span>
                  {text}
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          // 细粒度错误边界：单条消息渲染失败时只降级该条，不连累其他消息
          <ErrorBoundary
            key={msg.id}
            fallback={
              <div className="rounded-lg border border-amber-300/40 bg-amber-50/60 px-4 py-3 text-xs text-amber-700 transition-colors dark:border-amber-500/30 dark:bg-amber-900/20 dark:text-amber-300">
                ⚠️ 该条消息渲染失败（消息 ID: <code className="font-mono">{msg.id.slice(0, 12)}</code>），
                已被错误边界隔离。其它消息正常显示，可继续对话。
              </div>
            }
          >
            <ChatMessage message={msg} streaming={typingAssistantId === msg.id} />
          </ErrorBoundary>
        ))}
      </div>
    </div>
  );
}