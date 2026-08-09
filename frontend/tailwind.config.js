/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{js,ts,jsx,tsx}"],
  // 暗色模式由 <html class="dark"> 控制（App.tsx 提供切换开关），而非跟随系统
  darkMode: "class",
  theme: {
    extend: {
      // 品牌主色：科技蓝紫（CodeWise 主题基调）
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
        },
        // 语义色：成功 / 失败 / 警告
        success: "#10b981",
        danger: "#ef4444",
        warning: "#f59e0b",
      },
      // 字体：UI 界面 + 代码展示双体系
      fontFamily: {
        sans: ["Inter", "PingFang SC", "Microsoft YaHei", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Consolas", "monospace"],
      },
      // 动画：流式输出与执行状态指示
      animation: {
        typing: "typing 0.7s steps(1) infinite",      // 光标闪烁（LLM 生成中）
        "fade-in": "fadeIn 0.3s ease-out",            // 步骤卡片入场
      },
      keyframes: {
        typing: {
          "0%, 100%": { opacity: "0.2" },
          "50%": { opacity: "1" },
        },
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
