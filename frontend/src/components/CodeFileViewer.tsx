/**
 * 代码文件视图：IDE 风格展示 Agent 生成的 Python 代码。
 * 支持逐行打字机（lines 控制已显示行数）、行号、语法高亮与复制。
 */
import { memo, useMemo } from "react";
import Prism from "prismjs";
import "prismjs/components/prism-python";

interface CodeFileViewerProps {
  /** 完整最终代码 */
  code: string;
  /** 已显示行数（逐行打字机进度，0 ~ 总行数） */
  lines: number;
  /** 是否仍在流式生成（尾部显示打字光标） */
  streaming?: boolean;
}

export const CodeFileViewer = memo(function CodeFileViewer({
  code,
  lines,
  streaming = false,
}: CodeFileViewerProps) {
  const allLines = useMemo(() => code.split("\n"), [code]);
  const shown = Math.max(0, Math.min(lines, allLines.length));
  const visibleText = allLines.slice(0, shown).join("\n");
  const done = shown >= allLines.length;

  // 对已显示文本整体高亮后按行切分，保证跨行 token（如三引号字符串）着色正确
  const highlightedLines = useMemo(() => {
    if (!visibleText) return [];
    const html = Prism.highlight(visibleText, Prism.languages.python, "python");
    return html.split("\n");
  }, [visibleText]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      // 非安全上下文剪贴板不可用，静默失败
    }
  };

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-gray-800 bg-[#0f172a] shadow-sm">
      {/* 顶部标签栏：文件名 + 复制（验证状态属内部质量流程，不进用户界面） */}
      <div className="flex items-center gap-2 border-b border-white/10 bg-[#1e293b] px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-medium text-gray-200">
          <span className="text-brand-400">◆</span>
          solution.py
        </span>
        <span className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={handleCopy}
            className="rounded bg-white/10 px-2 py-0.5 text-[11px] text-gray-300 transition-colors hover:bg-white/20"
          >
            复制
          </button>
        </span>
      </div>

      {/* 代码区：行号 + 高亮行；max-h 让长代码在文件视图内独立滚动，不撑爆消息流 */}
      <div className="max-h-[70vh] overflow-auto">
        <pre className="py-3 text-[13px] leading-6">
          {highlightedLines.map((lineHtml, i) => (
            <div key={i} className="flex">
              <span className="w-12 shrink-0 select-none pr-4 text-right text-gray-600">
                {i + 1}
              </span>
              <span
                className="min-w-0 flex-1 whitespace-pre text-gray-100"
                dangerouslySetInnerHTML={{ __html: lineHtml || " " }}
              />
            </div>
          ))}
          {streaming && !done && (
            <div className="flex">
              <span className="w-12 shrink-0 select-none pr-4 text-right text-gray-600">
                {shown + 1}
              </span>
              <span className="typing-cursor mt-1 inline-block" />
            </div>
          )}
        </pre>
      </div>
    </div>
  );
});
