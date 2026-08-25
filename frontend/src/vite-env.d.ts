/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API Key（构建时注入；生产环境建议改用 localStorage["codewise_api_key"] 运行时注入） */
  readonly VITE_API_KEY?: string;
}
