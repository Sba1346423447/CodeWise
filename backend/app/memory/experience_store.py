"""长期经验库：ChromaDB 向量存储，跨会话复用历史反思经验。"""

import os
import uuid
from typing import Dict, List, Optional

import chromadb
from chromadb.config import Settings
from loguru import logger

# ChromaDB 独立服务地址，环境变量可覆盖（Docker 内为服务名 chromadb）
_CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
_CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
# 连接/读取超时（秒）：服务不可达时快速失败降级，避免阻塞后端启动
_CHROMA_TIMEOUT = 2
# 经验集合名称
_COLLECTION_NAME = "experiences"


class ExperienceStore:
    """长期经验库：add_experience 写入，retrieve_similar 语义召回，跨会话复用。

    使用 HttpClient 连接独立 ChromaDB 服务（与 docker-compose 四服务编排对齐）。
    """

    def __init__(self, host: str = _CHROMA_HOST, port: int = _CHROMA_PORT) -> None:
        # HttpClient 连接独立服务，数据由 chromadb 容器持久化；
        # 连接失败时降级为空库（读写均安全返回），保证后端可启动、可调试
        self._collection = None
        try:
            client = chromadb.HttpClient(
                host=host,
                port=port,
                settings=Settings(
                    chroma_server_connect_timeout=_CHROMA_TIMEOUT,
                    chroma_server_read_timeout=_CHROMA_TIMEOUT,
                ),
            )
            self._collection = client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            logger.warning(f"ChromaDB 连接失败（{host}:{port}），经验库降级为空：{exc}")

    def add_experience(
        self,
        task_desc: str,
        code: str,
        summary: str,
        experience_id: Optional[str] = None,
    ) -> str:
        """写入一条经验：task_desc 作为检索文本，code 与 summary 作为元数据。

        返回经验 ID，供删除或溯源使用；同 ID 重复写入自动覆盖。
        """
        doc_id = experience_id or uuid.uuid4().hex
        if self._collection is None:
            logger.warning("经验库未连接，跳过写入")
            return doc_id
        try:
            self._collection.upsert(
                ids=[doc_id],
                documents=[task_desc],
                metadatas=[{"code": code, "summary": summary}],
            )
        except Exception as exc:
            # 运行时服务不可达/异常时仅告警降级，不抛出，保证调用方（交付链路）不受影响
            logger.warning(f"经验写入失败，跳过：{exc}")
        return doc_id

    def retrieve_similar(self, task_desc: str, top_k: int = 3) -> List[Dict]:
        """按任务描述检索最相似的历史经验，按相似度升序返回（distance 越小越相似）。"""
        if self._collection is None:
            return []  # 经验库不可用时返回空结果
        result = self._collection.query(query_texts=[task_desc], n_results=top_k)

        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        experiences = []
        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) and metas[i] is not None else {}
            experiences.append(
                {
                    "id": doc_id,
                    "task_desc": docs[i] if i < len(docs) else "",
                    "code": meta.get("code", ""),
                    "summary": meta.get("summary", ""),
                    "distance": distances[i] if i < len(distances) else None,
                }
            )
        return experiences

    def delete_experience(self, experience_id: str) -> None:
        """按 ID 删除一条经验。"""
        if self._collection is not None:
            self._collection.delete(ids=[experience_id])

    def count(self) -> int:
        """当前经验总量。"""
        return self._collection.count() if self._collection is not None else 0
