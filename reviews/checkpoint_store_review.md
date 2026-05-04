REVIEW: orchid/checkpoint/store.py

Issue 1: _remove_entry calls _save_index on every removal
Line 178: self._save_index() is called inside _remove_entry.
PASS - This is correct. Every removal must persist the updated index to disk.

Issue 2: prune correctly keeps the most recent keep checkpoints
Line 130: to_remove = entries[keep:] after entries = self.list() (newest first).
PASS - self.list returns entries sorted newest-first. Slicing entries[keep:] correctly removes old entries.

Issue 3: load uses bare except Exception with noqa comment
Line 97: except Exception as exc:  # noqa: BLE001
PASS - Broad exception handler is intentional for robustness against corrupted files. noqa comment is present.

Issue 4: _load_index handles missing index file gracefully
Line 143: if not self._index_path.exists(): return []
PASS - Returns empty list on first run instead of crashing.

Summary
All 4 issues: PASS
