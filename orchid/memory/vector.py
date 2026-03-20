"""Vector memory — ChromaDB embedded mode for semantic search over sessions and decisions."""

from __future__ import annotations

import logging
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Metadata type keys
TYPE_SESSION_LOG = "session_log"
TYPE_DECISION = "decision"
TYPE_RESEARCH = "research"
TYPE_NOTE = "note"


_tiktoken_enc = None


def _count_tokens(text: str) -> int:
    """Count BPE tokens using tiktoken (cl100k_base) if available, else estimate.

    tiktoken is a transitive dependency of chromadb and is usually present.
    The cl100k_base encoding is a reasonable approximation for all models in use.
    Fallback: 1 token ≈ len(text) // 3 (conservative for code-heavy content).
    """
    global _tiktoken_enc  # noqa: PLW0603
    try:
        import tiktoken  # noqa: PLC0415
        if _tiktoken_enc is None:
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        return len(_tiktoken_enc.encode(text))
    except ImportError:
        return max(1, len(text) // 3)


def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Sliding-window BPE-token-aware chunker.

    chunk_size is a token limit.  Words are accumulated until adding the next
    word would exceed chunk_size tokens, then a new chunk begins with the last
    `overlap` words carried over.  This avoids the word-count approximation
    that can silently exceed embedding model token limits for code-heavy content.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = start + 1
        while end < len(words):
            candidate = " ".join(words[start : end + 1])
            if _count_tokens(candidate) > chunk_size:
                break
            end += 1

        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        if end >= len(words):
            break

        # Advance, keeping overlap words from the end of this chunk
        advance = max(1, (end - start) - overlap)
        start += advance

    return chunks


class VectorMemory:
    """
    ChromaDB-backed semantic memory, persistent at <project>/.orchid/chroma/.

    Gracefully degrades if chromadb or the embedding backend is unavailable:
    all operations become no-ops and warn once.
    """

    def __init__(
        self,
        project_dir: str | Path,
        collection_name: str = "orchid",
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self._client: Any = None
        self._collection: Any = None
        self._available = False
        self._warned = False
        self._unavailability_reason: str | None = None

        self._init()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init(self) -> None:
        try:
            import chromadb  # noqa: PLC0415

            chroma_dir = self.project_dir / ".orchid" / "chroma"
            chroma_dir.mkdir(parents=True, exist_ok=True)

            # Suppress noisy telemetry / deprecation warnings from chromadb
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._client = chromadb.PersistentClient(path=str(chroma_dir))

            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.debug(
                "VectorMemory ready: %s (%d items)",
                chroma_dir,
                self._collection.count(),
            )
        except ImportError:
            self._unavailability_reason = "import_error"
            logger.warning("chromadb not installed — vector memory disabled.")
        except Exception as exc:
            self._unavailability_reason = "runtime_error"
            logger.warning("VectorMemory init failed: %s — vector ops disabled.", exc)

    def _warn_once(self) -> None:
        if not self._warned:
            logger.warning("Vector memory not available; skipping operation.")
            self._warned = True

    # ── Embedding ─────────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Get embedding via tools.models.embed (handles llama.cpp + fallback)."""
        from orchid.tools.models import embed  # noqa: PLC0415
        return embed(text)

    # ── Core ops ──────────────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id_prefix: str | None = None,
    ) -> None:
        """Chunk text, embed each chunk, and store in ChromaDB."""
        if not self._available:
            self._warn_once()
            return

        meta = metadata or {}
        meta.setdefault("project", str(self.project_dir))
        meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        meta.setdefault("type", TYPE_NOTE)

        chunks = _chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            return

        prefix = doc_id_prefix or f"doc_{int(datetime.now(timezone.utc).timestamp())}"
        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for i, chunk in enumerate(chunks):
            try:
                emb = self._embed(chunk)
            except Exception as exc:
                logger.warning("Embedding failed for chunk %d: %s — skipping.", i, exc)
                continue
            ids.append(f"{prefix}_chunk{i}")
            embeddings.append(emb)
            documents.append(chunk)
            chunk_meta = dict(meta)
            chunk_meta["chunk_index"] = i
            chunk_meta["chunk_total"] = len(chunks)
            metadatas.append(chunk_meta)

        if ids:
            # Upsert so re-embedding the same session is idempotent
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            logger.debug("Stored %d chunk(s) under prefix '%s'.", len(ids), prefix)

    def query(
        self,
        text: str,
        n: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic search over stored documents.

        Returns list of {text, metadata, distance} dicts, best first.
        """
        if not self._available:
            self._warn_once()
            return []

        if self._collection.count() == 0:
            return []

        try:
            emb = self._embed(text)
        except Exception as exc:
            logger.warning("Embedding query failed: %s", exc)
            return []

        kwargs: dict[str, Any] = {
            "query_embeddings": [emb],
            "n_results": min(n, self._collection.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception as exc:
            logger.warning("ChromaDB query failed: %s", exc)
            return []

        out: list[dict[str, Any]] = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            out.append({"text": doc, "metadata": meta, "distance": dist})
        return out

    def add_session_log(
        self,
        session_id: str,
        log_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = metadata or {}
        meta["type"] = TYPE_SESSION_LOG
        meta["session_id"] = session_id
        self.add(log_text, metadata=meta, doc_id_prefix=f"session_{session_id}")

    def add_decision(
        self,
        decision_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = metadata or {}
        meta["type"] = TYPE_DECISION
        meta["decision_id"] = decision_id
        self.add(text, metadata=meta, doc_id_prefix=f"decision_{decision_id}")

    def count(self) -> int:
        if not self._available:
            return 0
        return self._collection.count()

    def close(self) -> None:
        """No-op — PersistentClient flushes on write; kept for API symmetry."""
        pass

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailability_reason(self) -> str | None:
        """'import_error', 'runtime_error', or None if available."""
        return self._unavailability_reason
