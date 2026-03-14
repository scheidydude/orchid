"""Vector memory stub — Chroma integration (implement when needed)."""

from __future__ import annotations


class VectorMemory:
    """
    Placeholder for semantic search over past sessions and decisions.

    Install extras: pip install orchid[vector]
    Then replace this stub with a real Chroma-backed implementation.
    """

    def __init__(self, collection: str = "orchid"):
        self._collection = collection
        self._available = self._check_available()

    def _check_available(self) -> bool:
        try:
            import chromadb  # noqa: F401
            return True
        except ImportError:
            return False

    def add(self, texts: list[str], ids: list[str], metadatas: list[dict] | None = None) -> None:
        if not self._available:
            return  # silently skip when chromadb not installed

        import chromadb
        client = chromadb.Client()
        col = client.get_or_create_collection(self._collection)
        col.add(documents=texts, ids=ids, metadatas=metadatas or [{} for _ in texts])

    def query(self, text: str, n: int = 5) -> list[str]:
        if not self._available:
            return []

        import chromadb
        client = chromadb.Client()
        col = client.get_or_create_collection(self._collection)
        results = col.query(query_texts=[text], n_results=n)
        return results.get("documents", [[]])[0]
