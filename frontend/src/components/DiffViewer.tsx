/**
 * 代码差异视图：展示 Agent 优化前后代码的行级 diff（新增/删除/未变）。
 * 使用自实现行级 LCS 算法，不引入第三方 diff 库，保持依赖简洁。
 */
import { memo, useMemo, useState } from "react";

interface DiffViewerProps {
  /** 修改前代码 */
  before: string;
  /** 修改后代码 */
  after: string;
}

/** 单行 diff 结果：操作类型 + 行内容（删除行取 before，新增行取 after，相同行为 before） */
interface DiffLine {
  type: "same" | "add" | "del";
  text: string;
}

/** 行级 LCS（最长公共子序列）diff：逐行比较两段代码，输出带操作标记的行序列 */
function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;

  // dp[i][j]：a[0..i) 与 b[0..j) 的 LCS 长度（经典动态规划）
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // 回溯构造 diff 序列（倒序收集后反转）
  const lines: DiffLine[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      lines.push({ type: "same", text: a[i - 1] });
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      lines.push({ type: "del", text: a[i - 1] });
      i--;
    } else {
      lines.push({ type: "add", text: b[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    lines.push({ type: "del", text: a[i - 1] });
    i--;
  }
  while (j > 0) {
    lines.push({ type: "add", text: b[j - 1] });
    j--;
  }
  return lines.reverse();
}

/** 行样式：删除红 / 新增绿 / 未变灰 */
const LINE_STYLES: Record<DiffLine["type"], string> = {
  del: "bg-rose-500/10 text-rose-300",
  add: "bg-emerald-500/10 text-emerald-300",
  same: "text-gray-400",
};

const LINE_MARK: Record<DiffLine["type"], string> = {
  del: "−",
  add: "+",
  same: " ",
};

export const DiffViewer = memo(function DiffViewer({ before, after }: DiffViewerProps) {
  const [open, setOpen] = useState(false);
  const lines = useMemo(() => diffLines(before, after), [before, after]);
  // 统计改动规模，仅在有实际变更时展示
  const changed = lines.filter((l) => l.type !== "same").length;

  if (changed === 0) return null;

  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-gray-800 bg-[#0f172a]">
      {/* 折叠触发头 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs text-gray-400 transition-colors hover:bg-white/5"
      >
        <span className={`text-[10px] text-gray-500 transition-transform duration-200 ${open ? "rotate-90" : ""}`}>▶</span>
        <span>本轮代码改动</span>
        <span className="ml-auto space-x-2">
          <span className="text-emerald-400">+{lines.filter((l) => l.type === "add").length}</span>
          <span className="text-rose-400">−{lines.filter((l) => l.type === "del").length}</span>
        </span>
      </button>

      {/* 展开区：diff 行（代码对比，暗色底色始终不变，与主题无关） */}
      {open && (
        <div className="overflow-x-auto border-t border-gray-800">
          <pre className="py-2 font-mono text-[12px] leading-6">
            {lines.map((line, index) => (
              <div key={index} className={`flex whitespace-pre ${LINE_STYLES[line.type]}`}>
                <span className="w-6 shrink-0 select-none pr-2 text-right text-gray-600">
                  {LINE_MARK[line.type]}
                </span>
                <span className="min-w-0 flex-1">{line.text || " "}</span>
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
});
