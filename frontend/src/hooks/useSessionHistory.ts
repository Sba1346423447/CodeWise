/**
 * 会话历史 Hook：管理会话列表的增删改查，与 /api/sessions 接口交互。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { createSession, deleteSession, listSessions } from "../utils/api";
import type { SessionInfo } from "../types/agent";

export function useSessionHistory() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 用 ref 缓存最新 sessions，避免连续删除时闭包旧值导致回滚错位
  const sessionsRef = useRef<SessionInfo[]>([]);
  sessionsRef.current = sessions;

  /** 拉取全部历史会话（最新在前） */
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSessions(await listSessions());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  /** 创建新会话（供"新建会话"入口使用），创建后刷新列表 */
  const create = useCallback(
    async (taskDesc: string) => {
      const session = await createSession(taskDesc);
      await refresh();
      return session;
    },
    [refresh],
  );

  /** 删除会话：乐观移除 + 失败回滚，保证 UI 即时响应。
   * 使用 ref 兜底，连续删除时回滚到当前最新列表（避免闭包旧值错位）。
   */
  const remove = useCallback(async (sessionId: string) => {
    const prev = sessionsRef.current;
    setSessions((current) => current.filter((s) => s.session_id !== sessionId));
    try {
      await deleteSession(sessionId);
    } catch (err) {
      setSessions(prev); // 删除失败恢复原列表
      setError((err as Error).message);
      throw err;
    }
  }, []);

  // 挂载时加载历史会话
  useEffect(() => {
    refresh();
  }, [refresh]);

  return { sessions, loading, error, refresh, create, remove };
}
