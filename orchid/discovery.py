"""Project auto-discovery via filesystem scanning and watchdog monitoring.

Architecture (D0033):
- ProjectDiscovery scans watch_dirs up to configured depth
- Uses watchdog inotify (Linux) for efficient file system events
- Non-recursive watching: one inotify fd per watched directory (not one per file)
- Watches watch_dir top-level + each direct child dir (depth 1)
- on_changed callback: debounced 2s, triggered by creation events → full rescan
- on_removed callback: debounced 2s per path, targeted — only fires if path is gone
- Explicit project paths bypass .orchid.yaml requirement
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_EXCLUDES = {".venv", "node_modules", ".git", "__pycache__", ".orchid"}


class ProjectDiscovery:
    """Scans directories for orchid projects and optionally watches for changes."""

    def __init__(
        self,
        watch_dirs: list[Path],
        explicit_projects: list[Path] | None = None,
        depth: int = 2,
        exclude: list[str] | None = None,
    ) -> None:
        self.watch_dirs = [Path(d).expanduser().resolve() for d in watch_dirs]
        self.explicit_projects = [
            Path(p).expanduser().resolve() for p in (explicit_projects or [])
        ]
        self.depth = depth
        self.exclude = set(exclude or _DEFAULT_EXCLUDES)
        self._observer = None
        self._debounce_timer: threading.Timer | None = None
        self._debounce_lock = threading.Lock()
        self._watched_paths: set[str] = set()
        self._watched_lock = threading.Lock()
        # Per-path debounce timers for targeted removal
        self._removal_timers: dict[str, threading.Timer] = {}
        self._removal_lock = threading.Lock()

    def is_orchid_project(self, path: Path) -> bool:
        """Return True if path/.orchid.yaml exists."""
        return (path / ".orchid.yaml").exists()

    def scan(self) -> list[Path]:
        """Walk watch_dirs up to depth, return all dirs containing .orchid.yaml.

        Always includes explicit_projects regardless of .orchid.yaml presence.
        Returns sorted list of unique project paths.
        """
        found: set[Path] = set()

        for watch_dir in self.watch_dirs:
            if not watch_dir.exists():
                logger.debug("Watch dir does not exist: %s", watch_dir)
                continue
            self._scan_dir(watch_dir, current_depth=0, found=found)

        # Merge with explicit projects (always included)
        result = list(found | set(self.explicit_projects))
        return sorted(result)

    def _scan_dir(self, directory: Path, current_depth: int, found: set[Path]) -> None:
        """Recursively scan directory for orchid projects."""
        if current_depth > self.depth:
            return

        try:
            for entry in sorted(directory.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name in self.exclude:
                    continue
                if self.is_orchid_project(entry):
                    found.add(entry.resolve())
                # Recurse into subdirs if we haven't reached the depth limit yet.
                # current_depth is the depth of the PARENT being scanned;
                # entries here are at depth current_depth+1 below watch_dir.
                # We recurse only if there is still room to go deeper.
                if current_depth + 1 < self.depth:
                    self._scan_dir(entry, current_depth + 1, found)
        except PermissionError:
            logger.debug("Permission denied scanning: %s", directory)
        except OSError as exc:
            logger.debug("Error scanning %s: %s", directory, exc)

    def watch(
        self,
        on_changed: Callable[[], None],
        on_removed: Callable[[Path], None] | None = None,
    ) -> None:
        """Start watchdog monitoring on watch_dirs.

        Two callbacks handle the two event directions independently:

        on_changed()
            Called (debounced 2s) when new projects may have appeared —
            directory created, .orchid.yaml created. The caller should
            re-scan and register newly found projects. Never used for
            deletions, so a momentarily incomplete scan cannot cause
            spurious unregistrations.

        on_removed(path: Path)
            Called per-path (debounced 2s) when a specific project
            directory or its .orchid.yaml was deleted. Only fires after
            confirming the path is actually gone from disk, eliminating
            both spurious watchdog startup events and transient rm -rf
            ordering effects. Never bulk-removes all projects.

        Uses non-recursive inotify watches to avoid exhausting the kernel
        inotify limit when watch_dirs contain large trees (.venv, node_modules).
        """
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning(
                "watchdog not installed; file watching disabled. "
                "Run: uv pip install 'watchdog>=4.0.0'"
            )
            return

        changed_debounce = 2.0

        # ── on_changed debounce (shared, resets on each new event) ───────────

        def _debounced_changed() -> None:
            with self._debounce_lock:
                self._debounce_timer = None
            try:
                on_changed()
            except Exception as exc:
                logger.exception("on_changed callback error: %s", exc)

        def _schedule_changed() -> None:
            with self._debounce_lock:
                if self._debounce_timer is not None:
                    self._debounce_timer.cancel()
                timer = threading.Timer(changed_debounce, _debounced_changed)
                timer.daemon = True
                self._debounce_timer = timer
                timer.start()

        # ── on_removed debounce (per-path, targeted) ─────────────────────────

        def _schedule_remove(path: Path) -> None:
            """Debounce a targeted removal for `path`.

            After 2s, checks whether path is truly gone before firing
            on_removed. This guards against:
            - Spurious watchdog startup events (path still exists → ignored)
            - Transient deletions during rm -rf ordering (path still exists)
            """
            if on_removed is None:
                return
            key = str(path)
            with self._removal_lock:
                existing = self._removal_timers.pop(key, None)
                if existing is not None:
                    existing.cancel()

            def _do_remove() -> None:
                with self._removal_lock:
                    self._removal_timers.pop(key, None)
                if not path.exists():
                    logger.info(
                        "Unregistering project %s — path no longer exists", path.name
                    )
                    try:
                        on_removed(path)
                    except Exception as exc:
                        logger.exception("on_removed callback error for %s: %s", path, exc)
                else:
                    logger.debug(
                        "Keeping project %s — path still exists (transient event)", path.name
                    )

            timer = threading.Timer(changed_debounce, _do_remove)
            timer.daemon = True
            with self._removal_lock:
                self._removal_timers[key] = timer
            timer.start()

        # ── Observer setup ───────────────────────────────────────────────────

        observer = Observer()

        def _add_watch(directory: Path) -> None:
            """Schedule a non-recursive watch on directory (idempotent)."""
            key = str(directory)
            with self._watched_lock:
                if key in self._watched_paths:
                    return
                self._watched_paths.add(key)
            try:
                observer.schedule(handler, key, recursive=False)
                logger.debug("Watching (non-recursive): %s", key)
            except Exception as exc:
                logger.debug("Could not watch %s: %s", key, exc)
                with self._watched_lock:
                    self._watched_paths.discard(key)

        class _Handler(FileSystemEventHandler):
            def on_created(self, event) -> None:
                path = Path(event.src_path)
                if event.is_directory:
                    # New subdirectory appeared — watch it for .orchid.yaml additions
                    if path.name not in self.exclude_set:
                        _add_watch(path)
                    # May already contain .orchid.yaml (copied tree)
                    _schedule_changed()
                elif path.name == ".orchid.yaml":
                    # .orchid.yaml created inside a watched dir (orchid init in-place)
                    logger.debug("Detected .orchid.yaml: %s", path)
                    _schedule_changed()

            def on_deleted(self, event) -> None:
                path = Path(event.src_path)
                # Guard: spurious watchdog startup events replay existing entries
                # as deletions. Bail out early if the path is still on disk.
                if path.exists():
                    return
                if event.is_directory:
                    # A specific directory was removed — schedule targeted removal.
                    # Never triggers a full rescan, so other projects are untouched.
                    _schedule_remove(path)
                elif path.name == ".orchid.yaml":
                    # Project's orchid config was deleted — the project dir is the parent.
                    _schedule_remove(path.parent)

        handler = _Handler()
        handler.exclude_set = self.exclude

        self._observer = observer

        for watch_dir in self.watch_dirs:
            if not watch_dir.exists():
                logger.warning("Watch dir does not exist, skipping: %s", watch_dir)
                continue

            # Watch top-level of watch_dir (catches new/deleted subdirs at depth 1)
            _add_watch(watch_dir)

            # Watch each existing direct child dir (catches .orchid.yaml additions
            # to pre-existing directories — e.g. user runs orchid init in place)
            try:
                for child in watch_dir.iterdir():
                    if child.is_dir() and child.name not in self.exclude:
                        _add_watch(child)
            except OSError as exc:
                logger.debug("Error listing %s for initial watches: %s", watch_dir, exc)

            logger.info("Watching for orchid projects in: %s", watch_dir)

        observer.start()
        logger.info("Project discovery watcher started (non-recursive)")

    def stop(self) -> None:
        """Stop the watchdog observer and cancel all pending timers."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass
            self._observer = None

        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None

        with self._removal_lock:
            for t in self._removal_timers.values():
                t.cancel()
            self._removal_timers.clear()

        with self._watched_lock:
            self._watched_paths.clear()

        logger.debug("Project discovery watcher stopped")
