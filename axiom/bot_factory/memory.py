"""Bot-specific ChromaDB memory management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class BotMemory:
    """Manages a bot-specific ChromaDB collection for persistent memory."""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self._collection = None
        self._client = None

    def _get_collection(self):
        """Lazily initialize ChromaDB client and collection."""
        if self._collection is not None:
            return self._collection

        import os

        if os.environ.get("AXIOM_DISABLE_CHROMA_IN_PROCESS") or os.environ.get("AXIOM_DISABLE_CHROMA"):
            # ISO-4: in-process ChromaDB can segfault on some hosts (ONNX/Arc-GPU).
            # When the guard is set, bot memory is disabled (recall/store no-op)
            # rather than risking an uncatchable native crash of the subprocess.
            return None

        try:
            import chromadb
            from axiom.config import AXIOM_HOME

            persist_dir = str(AXIOM_HOME / "chroma" / "bots")
            self._client = chromadb.PersistentClient(path=persist_dir)
            collection_name = f"bot-{self.bot_id[:32]}"
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"bot_id": self.bot_id},
            )
            return self._collection
        except ImportError:
            logger.warning("chromadb not installed — bot memory disabled")
            return None
        except Exception as e:
            logger.error("Failed to initialize bot memory for %s: %s", self.bot_id, e)
            return None

    def store(self, text: str, metadata: dict | None = None) -> None:
        """Store a text entry (observation, decision, reflection) in memory."""
        collection = self._get_collection()
        if collection is None:
            return

        from uuid import uuid4

        doc_id = str(uuid4())
        meta: dict[str, Any] = {
            "bot_id": self.bot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            meta.update(metadata)

        try:
            collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[doc_id],
            )
        except Exception as e:
            logger.error("Failed to store bot memory: %s", e)

    def recall(self, query: str, n_results: int = 5) -> list[dict]:
        """Search memory for entries relevant to the query."""
        collection = self._get_collection()
        if collection is None:
            return []

        try:
            results = collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            entries = []
            for i, doc in enumerate(results.get("documents", [[]])[0]):
                entry = {"text": doc}
                metadatas = results.get("metadatas", [[]])[0]
                if i < len(metadatas):
                    entry["metadata"] = metadatas[i]
                distances = results.get("distances", [[]])[0]
                if i < len(distances):
                    entry["relevance"] = 1.0 - distances[i]
                entries.append(entry)
            return entries
        except Exception as e:
            logger.error("Failed to recall bot memory: %s", e)
            return []

    def list_recent(self, limit: int = 50) -> list[dict]:
        """Return memory entries ordered newest-first by stored timestamp."""
        collection = self._get_collection()
        if collection is None:
            return []

        try:
            result = collection.get()
            docs = result.get("documents", []) or []
            metas = result.get("metadatas", []) or []
            ids = result.get("ids", []) or []
            entries: list[dict] = []
            for i, doc in enumerate(docs):
                entries.append({
                    "id": ids[i] if i < len(ids) else "",
                    "text": doc,
                    "metadata": metas[i] if i < len(metas) else {},
                })
            # Chroma get() has no ORDER BY; sort in Python by timestamp metadata
            entries.sort(
                key=lambda e: (e.get("metadata") or {}).get("timestamp", ""),
                reverse=True,
            )
            return entries[:limit]
        except Exception as e:
            logger.error("Failed to list bot memory: %s", e)
            return []

    def delete_collection(self) -> None:
        """Delete this bot's entire memory collection."""
        try:
            if self._client is None:
                import chromadb
                from axiom.config import AXIOM_HOME
                persist_dir = str(AXIOM_HOME / "chroma" / "bots")
                self._client = chromadb.PersistentClient(path=persist_dir)

            collection_name = f"bot-{self.bot_id[:32]}"
            try:
                self._client.delete_collection(collection_name)
            except Exception:
                pass
            self._collection = None
        except ImportError:
            pass
        except Exception as e:
            logger.error("Failed to delete bot memory collection: %s", e)
