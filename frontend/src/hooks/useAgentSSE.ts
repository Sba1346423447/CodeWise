/**
 * Agent SSE Hook：连接 POST /api/agent 的 SSE 流，支持多轮对话。
 *
 * 流式策略（与后端 agent.py 事件契约严格对齐）：
 * - agent_start：记录当前 sessionId（新建会话时后端返回）。
 * - node：实时聚合到当前助手消息的"行动摘要"（思考过程逐步出现）。
 * - done：携带最终结果，前端双路并行打字机渲染——
 *   ① 正文（总结 + 测试结论）逐字敲出；
 *   ② 最终代码以 IDE 风格"代码文件"视图逐行敲出。
 *   避免一次性抛结果，贴近 AI 编程工具的代码生成体验。
 *
 * 多轮：run(taskDesc, sessionId?)；复用 sessionId 时后端加载历史实现代码演进记忆。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentResult, SSEEvent, ThinkingItem } from "../types/agent";
import type { ChatMessageData } from "../components/ChatMessage";

/** SSE 运行状态 */
export type SSEStatus = "idle" | "running" | "done" | "error";

// 打字机速度：正文逐字推进间隔 / 代码逐行推进间隔（毫秒）
const TYPING_MS = 12;
const CODE_TYPING_MS = 30;

/** 解析单个 SSE 块（event: 行 + data: 行）为事件对象；解析失败返回 null */
function parseSSEBlock(block: string): SSEEvent | null {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { type: eventType as SSEEvent["type"], data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null; // 数据损坏的块直接丢弃
  }
}

/** 图节点名 → 行动摘要条目类型（与后端 orchestrator 提炼的 thinking 结构对齐） */
function nodeToThinkingType(node: string): ThinkingItem["type"] {
  const stepType = node.replace("_node", "");
  if (["tool", "test_gen", "test"].includes(stepType)) return "tool";
  if (stepType === "reflect") return "reflect";
  if (stepType === "refine" || stepType === "finalize") return "refine";
  return "react";
}

/** 图节点名 → 行动摘要标题 */
const NODE_LABELS: Record<string, string> = {
  react_node: "分析需求",
  tool_node: "执行工具",
  test_gen_node: "生成验证",
  test_node: "运行验证",
  reflect_node: "审查代码",
  refine_node: "优化代码",
  finalize_node: "交付结果",
};

/** 从 done 结果的 thinking 数组转成前端 ThinkingItem 列表（保留 diff 字段） */
function normalizeThinking(raw: unknown): ThinkingItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is ThinkingItem => !!item && typeof item === "object")
    .map((item) => ({
      type: (["react", "tool", "reflect", "refine"] as const).includes(item.type)
        ? item.type
        : "react",
      label: item.label || "",
      detail: item.detail || "",
      ...(item.diff && typeof item.diff === "object"
        ? { diff: { before: item.diff.before || "", after: item.diff.after || "" } }
        : {}),
    }));
}

export function useAgentSSE() {
  const [status, setStatus] = useState<SSEStatus>("idle");
  const [messages, setMessages] = useState<ChatMessageData[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** 正在打字机输出的助手消息 id（null = 无打字中消息），精确驱动光标/脉冲点/输入框解锁 */
  const [typingAssistantId, setTypingAssistantId] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string>("");
  const seqRef = useRef(0);
  // 双打字机：正文逐字 / 代码逐行
  const typingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const typingCodeRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** 停止两路打字机 */
  const stopTyping = useCallback(() => {
    if (typingRef.current) {
      clearInterval(typingRef.current);
      typingRef.current = null;
    }
    if (typingCodeRef.current) {
      clearInterval(typingCodeRef.current);
      typingCodeRef.current = null;
    }
  }, []);

  /** 正文逐字打字机：把 text 逐字写入目标消息的 content */
  const typeOutText = useCallback(
    (targetId: string, text: string) =>
      new Promise<void>((resolve) => {
        // 只清理自己的 interval：两路打字机并行时互不清掉对方的定时器
        if (typingRef.current) {
          clearInterval(typingRef.current);
          typingRef.current = null;
        }
        let i = 0;
        const timer = setInterval(() => {
          i += 2; // 每次推进 2 字符，兼顾流畅与性能
          setMessages((prev) =>
            prev.map((m) => (m.id === targetId ? { ...m, content: text.slice(0, i) } : m)),
          );
          if (i >= text.length) {
            clearInterval(timer);
            typingRef.current = null;
            resolve();
          }
        }, TYPING_MS);
        typingRef.current = timer;
      }),
    [],
  );

  /** 代码逐行打字机：把 codeLines 从 0 推进到总行数（CodeFileViewer 逐行渲染） */
  const typeOutCode = useCallback(
    (targetId: string, code: string) => {
      const total = code.split("\n").length;
      return new Promise<void>((resolve) => {
        // 只清理自己的 interval：两路打字机并行时互不清掉对方的定时器
        if (typingCodeRef.current) {
          clearInterval(typingCodeRef.current);
          typingCodeRef.current = null;
        }
        let line = 0;
        const timer = setInterval(() => {
          line += 1;
          setMessages((prev) =>
            prev.map((m) => (m.id === targetId ? { ...m, codeLines: line } : m)),
          );
          if (line >= total) {
            clearInterval(timer);
            typingCodeRef.current = null;
            resolve();
          }
        }, CODE_TYPING_MS);
        typingCodeRef.current = timer;
      });
    },
    [],
  );

  /** 执行一次 SSE 请求；返回是否正常结束 */
  const executeOnce = useCallback(
    async (
      taskDesc: string,
      sessionId: string,
      signal: AbortSignal,
      model?: string,
    ): Promise<boolean> => {
      const response = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_desc: taskDesc,
          session_id: sessionId || undefined,
          model: model || undefined,
        }),
        signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`请求失败（${response.status}）`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      // 本轮新增消息：占位用户消息 + 助手消息
      const userMsg: ChatMessageData = {
        id: `u-${Date.now()}-${++seqRef.current}`,
        role: "user",
        content: taskDesc,
      };
      const assistantMsg: ChatMessageData = {
        id: `a-${Date.now()}-${++seqRef.current}`,
        role: "assistant",
        content: "",
        thinking: [],
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      // 锁定"正在生成"的助手消息：光标/脉冲点/输入框禁用精确跟踪到该消息
      setTypingAssistantId(assistantMsg.id);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sepIndex: number;
        while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);
          const event = parseSSEBlock(block);
          if (!event) continue;

          switch (event.type) {
            case "agent_start":
              sessionIdRef.current = event.data.session_id;
              break;
            case "node": {
              // 逐步聚合行动摘要到当前助手消息（思考过程流式出现）
              const node = event.data.node as string;
              const item: ThinkingItem = {
                type: nodeToThinkingType(node),
                label: NODE_LABELS[node] ?? node.replace("_node", ""),
              };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, thinking: [...(m.thinking ?? []), item] }
                    : m,
                ),
              );
              break;
            }
            case "done": {
              const result = event.data as AgentResult;
              const finalCode = (result.final_code ?? "").trim();
              // 通用问答模式（无代码交付）：不挂载代码文件视图/测试徽章/测试面板，只展示正文。
              // 历史问答消息同样据此判断（见 App.tsx handleSelect）。
              const isAnswerOnly = !finalCode;
              // 注意：此处不立即置 done——打字机运行期间保持 running，
              // 让正文/代码的光标持续闪烁且输入框保持禁用，完成后才解锁。
              // 先挂载代码文件视图数据（lines=0 空行起步），再双路并行打字机
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsg.id
                    ? {
                        ...m,
                        code: isAnswerOnly ? undefined : finalCode,
                        codeLines: isAnswerOnly ? undefined : 0,
                        testsPassed: isAnswerOnly ? undefined : result.tests_passed,
                        reflections: result.reflection_count,
                        testResults: isAnswerOnly ? undefined : result.test_results,
                        elapsedMs: result.elapsed_ms,
                        model: result.model,
                      }
                    : m,
                ),
              );
              const summary = (result.final_message ?? "").trim();
              const verifyNote = isAnswerOnly
                ? ""
                : `${result.tests_passed ? "✅ 已完成" : "⚠️ 需要优化"} · 已优化 ${result.reflection_count} 轮`;
              const target = [summary, verifyNote].filter(Boolean).join("\n\n");
              const backendThinking = normalizeThinking(result.thinking);
              await Promise.all([
                typeOutText(assistantMsg.id, target),
                finalCode ? typeOutCode(assistantMsg.id, finalCode) : Promise.resolve(),
              ]);
              // 两路都完成后：注入最终行动摘要并解锁为 done（输入框恢复可用）。
              // 后端 thinking 非空时以其为准（含 detail/diff 字段）；为空时保留
              // 前端实时聚合的 thinking，避免折叠块被空数组覆盖而消失。
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, thinking: backendThinking.length > 0 ? backendThinking : m.thinking }
                    : m,
                ),
              );
              // 打字机全部收尾：先解除 typing 标记再置 done，保证光标/脉冲点与输入框同步解锁
              setTypingAssistantId(null);
              setStatus("done");
              return true;
            }
            case "error":
              setTypingAssistantId(null);
              setError(event.data.message);
              setStatus("error");
              return true;
          }
        }
      }
      // SSE 流正常关闭（无 done 事件）：仍需清 typing，避免脉冲点/输入框永久锁定
      setTypingAssistantId(null);
      setStatus("done");
      return true;
    },
    [typeOutText, typeOutCode],
  );

  /** 启动 Agent 任务：支持多轮（传入 sessionId 复用会话）；model 可选（用户选择模型） */
  const run = useCallback(
    async (taskDesc: string, sessionId?: string, model?: string) => {
      if (!taskDesc.trim()) return;
      stopTyping();
      setStatus("running");
      setError(null);
      seqRef.current = 0;
      sessionIdRef.current = sessionId ?? "";

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await executeOnce(taskDesc.trim(), sessionIdRef.current, controller.signal, model);
      } catch (err) {
        if (controller.signal.aborted) return; // 用户手动停止
        setError((err as Error).message);
        setStatus("error");
        setTypingAssistantId(null); // 解锁：脉冲点/输入框禁用同步解除
      }
    },
    [executeOnce, stopTyping],
  );

  /** 手动停止当前 SSE 流与打字机 */
  const stop = useCallback(() => {
    stopTyping();
    setTypingAssistantId(null);
    abortRef.current?.abort();
    setStatus("idle");
  }, [stopTyping]);

  /** 重置状态并清空消息（新建对话时调用） */
  const reset = useCallback(() => {
    stopTyping();
    setTypingAssistantId(null);
    setMessages([]);
    setStatus("idle");
    setError(null);
    sessionIdRef.current = "";
  }, [stopTyping]);

  /** 组件卸载或 stop/reset 时清理 */
  useEffect(() => {
    return () => {
      stopTyping();
      abortRef.current?.abort();
    };
  }, [stopTyping]);

  return {
    status,
    messages,
    error,
    run,
    stop,
    reset,
    typingAssistantId,
    sessionId: sessionIdRef.current,
  };
}
