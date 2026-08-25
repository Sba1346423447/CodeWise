import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// vitest 独立配置：jsdom 环境（hook 测试需要 DOM API 与 fake 环境隔离）
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test-setup.ts"],
  },
});
