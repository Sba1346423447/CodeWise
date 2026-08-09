/**
 * React 入口：挂载 App 组件到 DOM。
 */

import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./index.css";

// 根节点由 index.html 提供（非空断言：Vite 模板惯例，保证模板存在 #root）
const root = ReactDOM.createRoot(document.getElementById("root")!);

root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
