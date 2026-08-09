/**
 * 项目文件树：「项目」Tab 内容，展示 CodeWise 项目结构，支持文件夹展开折叠。
 * 数据为静态目录树（与 README 目录结构对齐）；后续可替换为后端 /api/project/tree 接口。
 */
import { useMemo, useState } from "react";

/** 树节点：目录或文件 */
interface TreeNode {
  name: string;
  type: "dir" | "file";
  children?: TreeNode[];
}

/** 静态项目结构（与仓库目录对齐，仅展示核心内容） */
const PROJECT_TREE: TreeNode[] = [
  {
    name: "databox",
    type: "dir",
    children: [
      {
        name: "backend",
        type: "dir",
        children: [
          { name: "app", type: "dir", children: [
            { name: "api", type: "dir", children: [
              { name: "agent.py", type: "file" },
              { name: "sessions.py", type: "file" },
              { name: "export.py", type: "file" },
            ]},
            { name: "core", type: "dir", children: [
              { name: "graph", type: "dir", children: [
                { name: "nodes.py", type: "file" },
                { name: "edges.py", type: "file" },
                { name: "builder.py", type: "file" },
                { name: "state.py", type: "file" },
              ]},
              { name: "tools", type: "dir", children: [
                { name: "code_executor.py", type: "file" },
                { name: "test_runner.py", type: "file" },
                { name: "linter.py", type: "file" },
                { name: "web_search.py", type: "file" },
              ]},
              { name: "orchestrator.py", type: "file" },
            ]},
            { name: "llm", type: "dir", children: [
              { name: "client.py", type: "file" },
              { name: "config.py", type: "file" },
            ]},
            { name: "memory", type: "dir", children: [
              { name: "conversation.py", type: "file" },
              { name: "experience_store.py", type: "file" },
            ]},
            { name: "models", type: "dir", children: [
              { name: "database.py", type: "file" },
              { name: "session.py", type: "file" },
              { name: "message.py", type: "file" },
            ]},
            { name: "main.py", type: "file" },
          ]},
          { name: "requirements.txt", type: "file" },
          { name: "Dockerfile", type: "file" },
        ],
      },
      {
        name: "frontend",
        type: "dir",
        children: [
          { name: "src", type: "dir", children: [
            { name: "components", type: "dir", children: [
              { name: "ChatMessage.tsx", type: "file" },
              { name: "CodeFileViewer.tsx", type: "file" },
              { name: "MessageList.tsx", type: "file" },
              { name: "SessionHistory.tsx", type: "file" },
            ]},
            { name: "hooks", type: "dir", children: [
              { name: "useAgentSSE.ts", type: "file" },
              { name: "useSessionHistory.ts", type: "file" },
            ]},
            { name: "App.tsx", type: "file" },
            { name: "main.tsx", type: "file" },
          ]},
          { name: "package.json", type: "file" },
          { name: "vite.config.ts", type: "file" },
        ],
      },
      { name: "config", type: "dir", children: [
        { name: "settings.yaml", type: "file" },
        { name: "prompts.yaml", type: "file" },
        { name: "tools.yaml", type: "file" },
      ]},
      { name: "docker-compose.yml", type: "file" },
      { name: "README.md", type: "file" },
      { name: "Makefile", type: "file" },
    ],
  },
];

/** 单个树节点：目录可展开折叠，文件为叶节点 */
function TreeItem({ node, depth }: { node: TreeNode; depth: number }) {
  const [open, setOpen] = useState(depth === 0); // 根目录默认展开
  const isDir = node.type === "dir";

  return (
    <div>
      <div
        className={`flex cursor-pointer select-none items-center gap-1.5 rounded px-2 py-1 text-[13px] transition-colors hover:bg-gray-100 dark:hover:bg-gray-800/60 ${
          isDir ? "text-gray-700 dark:text-gray-300" : "text-gray-500 dark:text-gray-400"
        }`}
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
        onClick={() => isDir && setOpen((v) => !v)}
      >
        <span className={`text-[10px] text-gray-400 transition-transform duration-150 ${isDir && open ? "rotate-90" : ""}`}>
          {isDir ? "▶" : "·"}
        </span>
        <span>{isDir ? "📁" : "📄"}</span>
        <span className="truncate">{node.name}</span>
      </div>
      {isDir && open && node.children?.map((child) => (
        <TreeItem key={child.name} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

export function FileTree() {
  const tree = useMemo(() => PROJECT_TREE, []);
  return (
    <div className="flex-1 overflow-y-auto px-2 py-2">
      <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-gray-400">
        项目文件
      </p>
      {tree.map((node) => (
        <TreeItem key={node.name} node={node} depth={0} />
      ))}
    </div>
  );
}
