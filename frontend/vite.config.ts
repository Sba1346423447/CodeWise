import { fileURLToPath } from "node:url";
import autoprefixer from "autoprefixer";
import react from "@vitejs/plugin-react";
import tailwindcss from "tailwindcss";
import { defineConfig } from "vite";

// 后端服务地址：开发代理目标（可用环境变量覆盖）
const BACKEND_TARGET = process.env.BACKEND_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  // Tailwind 必须在 PostCSS 中显式注册，否则 @tailwind/@apply 不会生效
  css: {
    postcss: {
      plugins: [tailwindcss(), autoprefixer()],
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND_TARGET, changeOrigin: true },
 },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: { vendor: ["react", "react-dom"] },
      },
    },
  },
});
