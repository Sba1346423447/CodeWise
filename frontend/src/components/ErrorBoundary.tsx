/**
 * 简易 React 错误边界：捕获子树渲染异常，避免整棵 React 树被卸载导致白屏。
 *
 * 触发场景示例：
 * - ReactMarkdown 解析超长 / 嵌套过深的 Markdown 时栈溢出
 * - Prism.highlight 处理超大代码块时栈溢出
 * - 任何子组件在 render 阶段抛错
 *
 * 设计取舍：
 * - 仅暴露最简 componentDidCatch，不引入 react-error-boundary 等第三方库（保持依赖最小化）
 * - 提供最小可恢复 UI：显示错误提示 + 刷新按钮，不暴露堆栈（避免给用户噪音信息）
 * - 同时通过 console.error 输出完整堆栈，便于开发者排查
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** 自定义降级 UI；缺省时显示内置的"页面出错了"提示 */
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    // 标记一次错误，触发下一次 render 走降级 UI
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 完整堆栈走控制台，前端用户只看到简洁提示
    console.error("[ErrorBoundary] 子树渲染异常：", error, info.componentStack);
  }

  /** 刷新页面，恢复正常渲染（局部异常时用户也可重试） */
  private handleReload = () => {
    this.setState({ hasError: false });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) return this.props.fallback;
      return (
        <div className="flex h-screen w-screen flex-col items-center justify-center gap-3 bg-[#FAFAF9] p-6 text-center text-gray-700 dark:bg-[#0b0f19] dark:text-gray-200">
          <div className="text-4xl">⚠️</div>
          <h2 className="text-lg font-semibold">页面出错了</h2>
          <p className="max-w-md text-sm leading-relaxed text-gray-500 dark:text-gray-400">
            渲染过程中发生异常，已被错误边界捕获。请点击下方按钮重试，或刷新页面恢复。
          </p>
          <button
            type="button"
            onClick={this.handleReload}
            className="mt-2 cursor-pointer rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-600"
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}