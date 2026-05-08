"""
向量索引层 — 基于 ChromaDB 的语义记忆搜索

设计：
- 每次 add/update 写入时自动创建 embedding
- 搜索时混合向量相似度和关键词匹配
- 支持跨命名空间搜索
"""

import json
from pathlib import Path
from typing import Optional

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False


class VectorIndex:
    """ChromaDB 向量索引包装"""

    def __init__(self, persist_path: Path, embedding_model: str = "all-MiniLM-L6-v2"):
        self.persist_path = persist_path
        self.embedding_model = embedding_model
        self._client: Optional["chromadb.ClientAPI"] = None
        self._collection: Optional["chromadb.Collection"] = None

    @property
    def client(self) -> "chromadb.ClientAPI":
        if self._client is None:
            if not HAS_CHROMA:
                raise ImportError("chromadb 未安装: pip install chromadb")
            self._client = chromadb.PersistentClient(
                path=str(self.persist_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    @property
    def collection(self) -> "chromadb.Collection":
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name="memfabric_memories",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add(self, event_id: str, key: str, value: str, namespace: str,
            agent_id: str, tags: list[str] | None = None):
        """添加向量索引"""
        try:
            self.collection.add(
                ids=[event_id],
                documents=[f"{key}: {value}"],
                metadatas=[{
                    "key": key,
                    "namespace": namespace,
                    "agent_id": agent_id,
                    "tags": json.dumps(tags or []),
                }],
            )
        except Exception as e:
            # chromadb ID 已存在时跳过
            if "already exists" not in str(e).lower():
                raise

    def update(self, event_id: str, key: str, value: str, namespace: str,
               agent_id: str, tags: list[str] | None = None):
        """更新向量索引（先删后加）"""
        try:
            self.collection.delete(ids=[event_id])
        except Exception:
            pass  # 可能不存在
        self.add(event_id, key, value, namespace, agent_id, tags)

    def remove(self, event_id: str):
        """移除向量索引"""
        try:
            self.collection.delete(ids=[event_id])
        except Exception:
            pass

    def search(self, query: str, namespace: str | list[str] | None = None,
               limit: int = 8, min_score: float = 0.0) -> list[dict]:
        """语义搜索，支持多命名空间过滤"""
        where = None
        if namespace:
            namespaces = [namespace] if isinstance(namespace, str) else namespace
            if len(namespaces) == 1:
                where = {"namespace": namespaces[0]}
            elif len(namespaces) > 1:
                where = {"$or": [{"namespace": ns} for ns in namespaces]}

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=limit,
                where=where,
            )
        except Exception:
            return []

        if not results["ids"] or not results["ids"][0]:
            return []

        out = []
        for i, doc_id in enumerate(results["ids"][0]):
            score = 1.0 - results["distances"][0][i] if results["distances"] else 1.0
            if score < min_score:
                continue
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            out.append({
                "event_id": doc_id,
                "key": meta.get("key", ""),
                "value": results["documents"][0][i] if results["documents"] else "",
                "namespace": meta.get("namespace", "default"),
                "agent_id": meta.get("agent_id", ""),
                "tags": json.loads(meta.get("tags", "[]")),
                "score": round(score, 4),
                "source": "vector",
            })
        return out

    def count(self) -> int:
        try:
            return self.collection.count()
        except Exception:
            return 0
