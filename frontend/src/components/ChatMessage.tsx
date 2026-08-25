/**
 * 消息气泡（对齐 WorkBuddy 规范）：
 * - 用户消息：靠右，浅灰背景圆角卡片，深灰文字，无描边
 * - AI 消息：靠左带头像，纯文本直接展示（无背景卡片）；
 *   开头为状态条（已完成 + 耗时），随后思考过程折叠条（浅灰小标题）；
 *   正文按模块分层渲染（结果结论 / 主推方案 / 参考方案 / 用法示例）；
 *   底部为操作栏（复制整条/重新生成/插入文件/分享）+ 元信息（耗时/模型/时间）。
 */
import { memo, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import Prism from "prismjs";
import type { ReactNode } from "react";
import type { TestResults, ThinkingItem } from "../types/agent";
import { CodeFileViewer } from "./CodeFileViewer";
import { ThinkingBlock } from "./ThinkingBlock";

/** 单条对话消息（与后端 messages 表 + 前端 state 对齐） */
export interface ChatMessageData {
  id: string;
  role: "user" | "assistant";
  /** 消息正文（助手为 Markdown；实时生成场景下仅含总结，代码走 code 字段） */
  content: string;
  /** 助手消息附带的行动摘要（流式过程中逐步聚合） */
  thinking?: ThinkingItem[];
  /** 实时生成：最终代码（以"代码文件"视图展示） */
  code?: string;
  /** 代码文件逐行打字机进度：已显示行数 */
  codeLines?: number;
  /** 测试是否通过（代码文件视图状态徽章） */
  testsPassed?: boolean;
  /** 反思轮数 */
  reflections?: number;
  /** 最终测试运行详情（实时生成时挂载，测试结果面板数据源） */
  testResults?: TestResults;
  /** 执行耗时（毫秒） */
  elapsedMs?: number;
  /** 使用的模型名 */
  model?: string;
}

interface ChatMessageProps {
  message: ChatMessageData;
  /** 该消息是否正在流式生成 */
  streaming?: boolean;
  /** 重新生成该消息（仅实时消息可用） */
  onRegenerate?: (id: string) => void;
}

/** 操作栏回调统一转为无参调用（由外层绑定具体消息） */
type ActionHandler = () => void;

/** 格式化耗时：<1s 显示 ms，否则显示秒 */
function formatElapsed(ms?: number): string {
  if (ms === undefined) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/** 安全转字符串：处理 ReactMarkdown 传入 children 为 undefined / 数组的情况 */
function toText(children: ReactNode): string {
  if (children === null || children === undefined) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(toText).join("");
  return "";
}

/** Prism 高亮代码块渲染器：浅灰圆角卡片 + 语言标注 + 复制按钮 */
function CodeBlock({ code }: { code: string }) {
  const html = useMemo(
    () => Prism.highlight(code, Prism.languages.python, "python"),
    [code],
  );

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      // 非安全上下文剪贴板不可用，静默失败
    }
  };

  return (
    <div className="group relative my-2 overflow-hidden rounded-lg bg-gray-100 transition-colors dark:bg-gray-800">
      {/* 语言标注 + 复制按钮 */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-gray-200/50 px-3 py-1 text-xs dark:border-gray-700 dark:bg-gray-800">
        <span className="text-gray-500 dark:text-gray-400">python</span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-gray-400 opacity-0 transition-opacity hover:text-gray-700 group-hover:opacity-100 dark:hover:text-gray-200"
          title="复制代码"
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg>
        </button>
      </div>
      <div
        className="overflow-auto p-3 font-mono text-[13px] leading-relaxed text-gray-800 dark:text-gray-100"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}

/** AI 消息底部操作栏：复制整条 / 重新生成（分享/插入暂未接线，隐藏或置灰避免空壳） */
function MessageActions({
  onCopy,
  copied = false,
  onRegenerate,
}: {
  onCopy: ActionHandler;
  /** 复制成功轻提示：短暂显示"已复制" */
  copied?: boolean;
  onRegenerate?: ActionHandler;
}) {
  const btnCls =
    "flex items-center gap-1 rounded px-1.5 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300";
  const disabledBtnCls =
    "flex cursor-not-allowed items-center gap-1 rounded px-1.5 py-1 text-xs text-gray-300 dark:text-gray-600";
  return (
    <div className="mt-1 flex items-center gap-1">
      <button type="button" onClick={onCopy} className={btnCls} title="复制整条回复">
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg>
        {copied ? "已复制" : "复制"}
      </button>
      {onRegenerate && (
        <button type="button" onClick={onRegenerate} className={btnCls} title="重新生成">
          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
          重新生成
        </button>
      )}
      {/* 插入到文件：依赖后端文件编辑能力（阶段 2 再接入），当前隐藏 */}
      {/* 分享：暂未实现后端分享链路，置灰提示即将支持 */}
      <span className={disabledBtnCls} title="分享功能即将支持">
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.59 13.51 6.83 3.98" /><path d="m15.41 6.51-6.82 3.98" /></svg>
        分享
      </span>
    </div>
  );
}

function ChatMessageInner({ message, streaming = false, onRegenerate }: ChatMessageProps) {
  const isUser = message.role === "user";
  // Hooks 必须无条件调用（rules-of-hooks）：复制状态提前声明，用户消息不使用
  const [copied, setCopied] = useState(false);

  // 用户消息：靠右，浅灰背景圆角卡片，深灰文字，无描边（低饱和克制）
  if (isUser) {
    return (
      <div className="flex items-start justify-end gap-2">
        <div className="max-w-[80%] rounded-2xl bg-gray-200/70 px-4 py-2.5 text-[15px] leading-relaxed text-gray-800 transition-colors dark:bg-gray-800 dark:text-gray-100">
          {message.content}
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-300/70 text-xs font-semibold text-gray-600 transition-colors dark:bg-gray-700 dark:text-gray-300">
          我
        </div>
      </div>
    );
  }

  // AI 消息：靠左带头像，纯文本不加背景卡片
  const copyWhole = async () => {
    const text = [message.content, message.code].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      // 轻提示：2 秒后恢复"复制"文案
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板不可用静默失败
    }
  };

  return (
    <div className="flex items-start justify-start gap-2">
      {/* 助手品牌头像 */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white shadow-sm">
        ⌘
      </div>

      <div className="w-full max-w-[85%] pt-1">
        {/* ① 状态条：只展示耗时（验证结果是内部质量流程，不进用户界面） */}
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          {message.elapsedMs !== undefined && (
            <span className="text-gray-400 dark:text-gray-500">耗时 {formatElapsed(message.elapsedMs)}</span>
          )}
        </div>

        {/* ② 思考过程：默认折叠，浅灰小标题条（放回复最开头，不占主视觉） */}
        {message.thinking && message.thinking.length > 0 && (
          <ThinkingBlock items={message.thinking} loading={streaming} />
        )}

        {/* ③ 正文：Markdown 渲染，代码块走自定义高亮（结构模块由内容组织） */}
        <div className="mt-2 text-[15px] leading-relaxed text-gray-800 dark:text-gray-200">
          <ReactMarkdown
            components={{
              code({ className, children, ...props }) {
                const text = toText(children);
                const match = /language-(\w+)/.exec(className || "");
                // 修复：children 之前直接当字符串调 .includes，当 ReactMarkdown 传入
                // undefined 或数组时会抛 TypeError 把整棵树炸掉。统一走 toText 转字符串
                // 并用 typeof 守卫 isBlock 判断，避免空值/非字符串场景崩溃。
                const isBlock = !!match && text.includes("\n");
                if (isBlock) {
                  return <CodeBlock code={text.replace(/\n$/, "")} />;
                }
                return (
                  <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[13px] text-brand-700 dark:bg-gray-800 dark:text-brand-300" {...props}>
                    {children}
                  </code>
                );
              },
              p({ children }) {
                return <p className="mb-3 last:mb-0">{children}</p>;
              },
              pre({ children }) {
                return <>{children}</>;
              },
            }}
          >
            {message.content || " "}
          </ReactMarkdown>
        </div>

        {/* 流式生成：打字光标指示 */}
        {streaming && (
          <span className="typing-cursor ml-0.5 mt-1 inline-block" aria-hidden="true" />
        )}

        {/* ④ 主推方案：最终代码以 IDE 风格文件视图呈现（逐行打字机） */}
        {message.code !== undefined && (
          <div className="mt-3">
            <p className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300">
              主推：最终实现
            </p>
            <CodeFileViewer
              code={message.code}
              lines={message.codeLines ?? 0}
              streaming={streaming}
            />
          </div>
        )}

        {/* ⑤ 消息底部：操作栏 + 元信息（验证详情属内部质量流程，不渲染） */}
        <div className="mt-2 flex items-center justify-between">
          <MessageActions
            onCopy={copyWhole}
            copied={copied}
            onRegenerate={onRegenerate ? () => onRegenerate(message.id) : undefined}
          />
          <span className="flex items-center gap-2 text-[11px] text-gray-400 dark:text-gray-500">
            {message.model && <span>{message.model}</span>}
            {message.elapsedMs !== undefined && <span>{formatElapsed(message.elapsedMs)}</span>}
          </span>
        </div>
      </div>
    </div>
  );
}

/** 用户消息内容不变时跳过重渲染，减少长会话开销 */
export const ChatMessage = memo(ChatMessageInner);