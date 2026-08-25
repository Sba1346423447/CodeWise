/**
 * useAgentSSE hook 测试：mock fetch 返回 SSE 流，验证事件解析 / 消息组装 /
 * 状态流转 / 停止与重置。
 *
 * 打字机间隔短（12ms/字符），用短文本让真实定时器自然跑完，不引入 fake timers。
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentSSE } from "./useAgentSSE";

/** 构造一段 SSE 响应体（多个事件块拼接） */
function sseBody(events: Array<[string, unknown]>): string {
  return events
    .map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

/** mock fetch 返回指定 SSE 文本的流式响应 */
function mockSSEFetch(events: Array<[string, unknown]>) {
  const body = sseBody(events);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return vi.fn().mockResolvedValue({
    ok: true,
    body: stream,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useAgentSSE", () => {
  it("完整事件流：解析 agent_start/node/done，组装消息并置 done", async () => {
    vi.stubGlobal(
      "fetch",
      mockSSEFetch([
        ["agent_start", { session_id: "s-1", run_id: "r-1" }],
        ["node", { node: "react_node", update: {} }],
        [
          "done",
          {
            final_code: "print(1)",
            final_message: "完成",
            tests_passed: true,
            reflection_count: 1,
            test_results: { passed: 1 },
            model: "m",
            elapsed_ms: 5,
            thinking: [{ type: "react", label: "分析需求", detail: "..." }],
          },
        ],
      ]),
    );

    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("写个程序");
    });

    // 用户消息 + 助手消息
    expect(result.current.messages).toHaveLength(2);
    const [userMsg, aiMsg] = result.current.messages;
    expect(userMsg.role).toBe("user");
    expect(aiMsg.role).toBe("assistant");
    // session_id / 行动摘要 / 代码与测试结论已注入
    expect(result.current.sessionId).toBe("s-1");
    expect(aiMsg.thinking).toHaveLength(1);
    expect(aiMsg.code).toBe("print(1)");
    expect(aiMsg.testsPassed).toBe(true);
    // 打字机收尾后状态与解锁
    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.typingAssistantId).toBeNull();
  });

  it("通用问答（无代码）：不挂代码视图，正文即回答", async () => {
    vi.stubGlobal(
      "fetch",
      mockSSEFetch([
        ["agent_start", { session_id: "s-2", run_id: "r-2" }],
        ["done", { final_code: "", final_message: "直接回答", tests_passed: false }],
      ]),
    );

    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("解释概念");
    });

    const aiMsg = result.current.messages.at(-1)!;
    expect(aiMsg.code).toBeUndefined();
    expect(aiMsg.content).toContain("直接回答");
    await waitFor(() => expect(result.current.status).toBe("done"));
  });

  it("confirmation_required：记录挂起信息供确认对话框渲染", async () => {
    vi.stubGlobal(
      "fetch",
      mockSSEFetch([
        ["agent_start", { session_id: "s-3", run_id: "r-3" }],
        ["confirmation_required", { run_id: "r-3", tools: [{ name: "web_search" }] }],
        ["done", { final_code: "", final_message: "等待确认", pending_confirmation: {} }],
      ]),
    );

    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("联网搜索任务");
    });

    expect(result.current.pendingConfirmation?.run_id).toBe("r-3");
  });

  it("error 事件：置错误状态", async () => {
    vi.stubGlobal(
      "fetch",
      mockSSEFetch([
        ["agent_start", { session_id: "s-4", run_id: "r-4" }],
        ["error", { message: "模型调用失败" }],
      ]),
    );

    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("任务");
    });

    expect(result.current.error).toBe("模型调用失败");
    expect(result.current.status).toBe("error");
  });

  it("run 空白输入：不发起请求", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("   ");
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("reset：清空消息与状态", async () => {
    vi.stubGlobal(
      "fetch",
      mockSSEFetch([
        ["agent_start", { session_id: "s-5", run_id: "r-5" }],
        ["done", { final_code: "", final_message: "ok" }],
      ]),
    );
    const { result } = renderHook(() => useAgentSSE());
    await act(async () => {
      await result.current.run("任务");
    });
    expect(result.current.messages.length).toBeGreaterThan(0);

    act(() => result.current.reset());
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.status).toBe("idle");
  });

  it("stop：通知后端取消并复位状态", async () => {
    const stopFetch = vi.fn().mockResolvedValue({ ok: true });
    // 主流发出 agent_start 后挂起不结束，模拟运行中被停止；
    // 监听 abort 信号让 reader.read() 抛错（真实 fetch abort 行为）
    const mainFetch = vi.fn((_url: string, init?: { signal?: AbortSignal }) => {
      const pendingStream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              `event: agent_start\ndata: ${JSON.stringify({ session_id: "s-6", run_id: "r-6" })}\n\n`,
            ),
          );
          /* 不关闭：模拟长任务流 */
          init?.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve({ ok: true, body: pendingStream });
    });
    vi.stubGlobal("fetch", mainFetch);

    const { result } = renderHook(() => useAgentSSE());
    let runPromise: Promise<void> | undefined;
    act(() => {
      runPromise = result.current.run("长任务");
    });
    await waitFor(() => expect(result.current.status).toBe("running"));

    vi.stubGlobal("fetch", stopFetch);
    act(() => result.current.stop());

    expect(stopFetch).toHaveBeenCalledWith(
      "/api/agent/stop",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result.current.status).toBe("idle");
    await act(async () => {
      await runPromise;
    });
  });
});
