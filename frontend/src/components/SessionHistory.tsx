/**
 * 会话历史：左侧栏「对话」Tab 内容，展示历史会话列表，按时间分组，支持切换与删除。
 * 说明：品牌区（Logo/新建对话）已上移至 App 侧栏顶部，本组件只负责历史列表。
 */
import { useMemo } from "react";

import type { SessionInfo } from "../types/agent";

interface SessionHistoryProps {
  sessions: SessionInfo[];
  /** 当前选中会话 ID（高亮显示） */
  activeSessionId?: string | null;
  /** 切换会话 */
  onSelect: (sessionId: string) => void;
  /** 删除会话 */
  onDelete: (sessionId: string) => void;
  /** 历史加载中 */
  loading?: boolean;
}

/** 时间格式化为 MM-DD HH:mm */
function formatTime(createdAt: string): string {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 是否为同一天 */
function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** 按时间分组：今天 / 更早（分组顺序固定，组内倒序由接口保证） */
function groupSessions(sessions: SessionInfo[]): Array<{ label: string; items: SessionInfo[] }> {
  const now = new Date();
  const today: SessionInfo[] = [];
  const earlier: SessionInfo[] = [];

  for (const session of sessions) {
    const date = new Date(session.created_at);
    if (Number.isNaN(date.getTime())) {
      earlier.push(session);
    } else if (isSameDay(date, now)) {
      today.push(session);
    } else {
      earlier.push(session);
    }
  }

  return [
    { label: "今天", items: today },
    { label: "更早", items: earlier },
  ];
}

export function SessionHistory({
  sessions,
  activeSessionId,
  onSelect,
  onDelete,
  loading = false,
}: SessionHistoryProps) {
  const groups = useMemo(() => groupSessions(sessions), [sessions]);

  return (
    <div className="flex h-full flex-col">
      {/* 分组会话列表 */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {loading && (
          <p className="px-2 py-4 text-center text-xs text-gray-400">加载中...</p>
        )}

        {!loading && sessions.length === 0 && (
          <p className="px-2 py-4 text-center text-xs text-gray-400">暂无历史会话</p>
        )}

        {groups.map(
          (group) =>
            group.items.length > 0 && (
              <div key={group.label} className="mb-2">
                <p className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                  {group.label}
                </p>
                {group.items.map((session) => {
                  const active = session.session_id === activeSessionId;
                  return (
                    <div
                      key={session.session_id}
                      onClick={() => onSelect(session.session_id)}
                      className={`group flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors ${
                        active
                          ? "bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
                          : "text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800/60"
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm">{session.task_desc || "未命名对话"}</p>
                        <p className="flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500">
                          <span
                            className={`inline-block h-1.5 w-1.5 rounded-full ${
                              session.status === "running"
                                ? "bg-brand-500"
                                : session.status === "failed"
                                  ? "bg-danger"
                                  : "bg-gray-300 dark:bg-gray-600"
                            }`}
                          />
                          {formatTime(session.created_at)}
                        </p>
                      </div>

                      {/* 删除按钮：始终可见（弱化灰色），hover 加深，避免 hidden+group-hover 组合失效导致无法点击 */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation(); // 阻止冒泡触发切换
                          onDelete(session.session_id);
                        }}
                        className="ml-2 shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-200 hover:text-danger dark:text-gray-500 dark:hover:bg-gray-700 dark:hover:text-danger"
                        aria-label={`删除会话：${session.task_desc}`}
                      >
                        ✕
                      </button>
                    </div>
                  );
                })}
              </div>
            ),
        )}
      </div>
    </div>
  );
}
