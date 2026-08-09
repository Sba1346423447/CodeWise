/**
 * 代码查看器：语法高亮展示 Agent 生成的 Python 代码，支持一键复制。
 */

import { useMemo, useState } from "react";
import Prism from "prismjs";
import "prismjs/components/prism-python";

interface CodeViewerProps {
  code: string;
  /** 高亮语言，默认 python */
  language?: string;
}

export function CodeViewer({ code, language = "python" }: CodeViewerProps) {
  const [copied, setCopied] = useState(false);

  // 仅当 code 变化时重新高亮（避免每次渲染重复计算）
  const html = useMemo(() => {
    if (!code.trim()) return "";
    const grammar = Prism.languages[language] ?? Prism.languages.python;
    return Prism.highlight(code, grammar, language);
  }, [code, language]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 非安全上下文（无 https/localhost）剪贴板不可用，静默失败
    }
  };

  return (
    <div className="relative">
      {/* 深色代码容器（index.css 的 .code-block） */}
      <div className="code-block">
        {html ? (
          <div
            className="overflow-auto"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ) : (
          <p className="text-gray-400">暂无代码，Agent 生成后在此展示。</p>
        )}
      </div>

      {/* 复制按钮：固定于容器右上角 */}
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-3 top-3 rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600"
      >
        {copied ? "已复制" : "复制"}
      </button>
    </div>
  );
}
