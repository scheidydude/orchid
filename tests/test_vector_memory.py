"""Tests for memory/vector.py — ChromaDB-backed semantic search."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_vm(tmp_path, chunk_size=512, chunk_overlap=64):
    """Create a VectorMemory pointing at tmp_path with embedding mocked."""
    from orchid.memory.vector import VectorMemory
    vm = VectorMemory(project_dir=tmp_path, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return vm


def _fake_embed(text: str) -> list[float]:
    """Deterministic fake embedding: hash-based unit vector of dim 8."""
    seed = sum(ord(c) for c in text[:64])
    import math
    vals = [math.sin(seed * (i + 1)) for i in range(8)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


# ── Chunking unit tests ───────────────────────────────────────────────────────

def test_chunk_empty():
    from orchid.memory.vector import _chunk_text
    assert _chunk_text("") == []


def test_chunk_short_text():
    from orchid.memory.vector import _chunk_text
    result = _chunk_text("hello world", chunk_size=512, overlap=64)
    assert result == ["hello world"]


def test_chunk_overlap():
    from orchid.memory.vector import _chunk_text
    tokens = list(range(100))
    text = " ".join(str(t) for t in tokens)
    chunks = _chunk_text(text, chunk_size=10, overlap=3)
    # Verify overlap: last 3 tokens of chunk[0] == first 3 tokens of chunk[1]
    c0_tokens = chunks[0].split()
    c1_tokens = chunks[1].split()
    assert c0_tokens[-3:] == c1_tokens[:3]


def test_chunk_large_text():
    from orchid.memory.vector import _chunk_text
    text = " ".join(["word"] * 2000)
    chunks = _chunk_text(text, chunk_size=512, overlap=64)
    assert len(chunks) > 1
    # All tokens accounted for (some are repeated due to overlap — just check coverage)
    for chunk in chunks:
        assert len(chunk.split()) <= 512


# ── VectorMemory integration tests ───────────────────────────────────────────

def test_add_and_query(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        assert vm.available

        vm.add("session compression improves performance", metadata={"type": "session_log"})
        vm.add("chroma vector database integration", metadata={"type": "note"})
        vm.add("decision log for architectural choices", metadata={"type": "decision"})

        results = vm.query("session compression")
        assert len(results) > 0
        assert all("text" in r and "metadata" in r and "distance" in r for r in results)


def test_query_returns_distance_and_metadata(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        vm.add("test document about memory", metadata={"type": "note", "source": "test"})
        results = vm.query("memory", n=1)
        assert len(results) == 1
        r = results[0]
        assert 0.0 <= r["distance"] <= 2.0  # cosine distance in [0, 2]
        assert r["metadata"].get("type") == "note"
        assert r["metadata"].get("source") == "test"


def test_chunking_long_document(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path, chunk_size=10, chunk_overlap=2)
        long_text = " ".join([f"token{i}" for i in range(50)])
        vm.add(long_text, metadata={"type": "note"}, doc_id_prefix="long_doc")
        # Should have stored multiple chunks
        assert vm.count() > 1


def test_metadata_project_auto_set(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        vm.add("metadata auto-population test")
        results = vm.query("metadata", n=1)
        assert len(results) == 1
        assert results[0]["metadata"]["project"] == str(tmp_path)


def test_add_session_log(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        vm.add_session_log(
            session_id="session_20260314_120000",
            log_text="Completed task T007: verified embedding endpoint",
            metadata={"project": "orchid"},
        )
        results = vm.query("embedding endpoint", n=3)
        assert any(r["metadata"].get("type") == "session_log" for r in results)
        assert any(r["metadata"].get("session_id") == "session_20260314_120000" for r in results)


def test_add_decision(tmp_path):
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        vm.add_decision(
            decision_id="D0007",
            text="Chroma embedded mode chosen over server mode (no extra infra).",
        )
        results = vm.query("Chroma embedded mode", n=3)
        assert any(r["metadata"].get("type") == "decision" for r in results)
        assert any(r["metadata"].get("decision_id") == "D0007" for r in results)


def test_upsert_idempotent(tmp_path):
    """Adding the same doc_id_prefix twice should not double the count."""
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = _make_vm(tmp_path)
        vm.add("idempotency test document", doc_id_prefix="idem_test")
        count_after_first = vm.count()
        vm.add("idempotency test document", doc_id_prefix="idem_test")
        count_after_second = vm.count()
        assert count_after_second == count_after_first


# ── Graceful degradation tests ────────────────────────────────────────────────

def test_graceful_degradation_when_no_embedder(tmp_path):
    """Vector ops should silently no-op when embed() raises RuntimeError."""
    with patch("orchid.tools.models.embed", side_effect=RuntimeError("no embedder")):
        from orchid.memory.vector import VectorMemory
        vm = VectorMemory(project_dir=tmp_path)
        assert vm.available  # chromadb itself is fine
        # add() should not raise even if embedding fails
        vm.add("this should not crash")
        # query() should not raise
        results = vm.query("anything")
        assert results == []


def test_graceful_degradation_when_chromadb_missing(tmp_path, monkeypatch):
    """If chromadb import fails, VectorMemory.available should be False and ops no-op."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "chromadb":
            raise ImportError("chromadb not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from orchid.memory.vector import VectorMemory
    vm = VectorMemory(project_dir=tmp_path)
    assert not vm.available
    vm.add("should not crash")
    assert vm.query("anything") == []
    assert vm.count() == 0


# ── Session recall integration ────────────────────────────────────────────────

def test_session_recall_returns_formatted_string(tmp_path):
    """Session.recall() should return a formatted Recalled Context block."""
    # Bootstrap minimal project structure
    (tmp_path / "tasks.md").write_text("", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")

    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        from orchid.session import Session
        s = Session(project_dir=tmp_path)
        s.load()

        if not s._vector or not s._vector.available:
            pytest.skip("Vector memory not available")

        s._vector.add("hot compression improves memory", metadata={"type": "session_log"})
        result = s.recall("compression")
        assert "## Recalled Context" in result
        assert "session_log" in result
