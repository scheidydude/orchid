"""Consistency checker — scan .py and .js files for broken relative imports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Python: relative imports (from .x import y, from ..pkg.mod import z)
_PY_REL_IMPORT_RE = re.compile(
    r"^\s*from\s+(\.+[\w.]*)\s+import\s+",
    re.MULTILINE,
)

# JS/TS: import/export ... from './path' or require('./path')
_JS_IMPORT_FROM_RE = re.compile(
    r"""(?:import|export)\s+.*?from\s+['"](\./[^'"]+|\.\.\/[^'"]+)['"]""",
    re.MULTILINE,
)
_JS_REQUIRE_RE = re.compile(
    r"""require\s*\(\s*['"](\./[^'"]+|\.\.\/[^'"]+)['"]\s*\)""",
    re.MULTILINE,
)

_SKIP_DIRS = {"node_modules", ".venv", "dist", "build", "__pycache__", ".git", ".orchid"}


def check_imports(project_path: str | Path) -> list[dict[str, Any]]:
    """
    Scan all .py and .js files under project_path for broken relative imports.

    Returns a list of dicts: {file, import, expected_path, exists}.
    Only broken (non-existent) imports are returned.
    """
    root = Path(project_path).resolve()
    broken: list[dict[str, Any]] = []

    for fpath in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in fpath.parts):
            continue
        broken.extend(_check_py_file(fpath, root))

    for fpath in root.rglob("*.js"):
        if any(part in _SKIP_DIRS for part in fpath.parts):
            continue
        broken.extend(_check_js_file(fpath, root))

    return broken


def check_imports_summary(project_path: str | Path) -> str:
    """Return a human-readable import check report (for use as a ReAct tool)."""
    broken = check_imports(project_path)
    if not broken:
        return "Import check passed — no broken relative imports found."
    lines = [f"Found {len(broken)} broken import(s):"]
    for b in broken:
        lines.append(f"  {b['file']}: import '{b['import']}' → {b['expected_path']} (not found)")
    return "\n".join(lines)


def _check_py_file(fpath: Path, root: Path) -> list[dict[str, Any]]:
    """Check relative imports in a Python file."""
    broken = []
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    file_dir = fpath.parent

    for m in _PY_REL_IMPORT_RE.finditer(content):
        import_str = m.group(1)  # e.g. ".sibling" or "..parent.module"

        # Count leading dots to determine relative depth
        dots = len(import_str) - len(import_str.lstrip("."))
        module_part = import_str.lstrip(".")

        # Walk up directories based on dot count
        base = file_dir
        for _ in range(dots - 1):
            base = base.parent

        if module_part:
            candidate = base / Path(*module_part.split("."))
            expected_file = candidate.with_suffix(".py")
            expected_pkg = candidate / "__init__.py"

            if not expected_file.exists() and not expected_pkg.exists():
                try:
                    rel = str(fpath.relative_to(root))
                    expected = str(expected_file.relative_to(root))
                except ValueError:
                    rel = str(fpath)
                    expected = str(expected_file)
                broken.append({
                    "file": rel,
                    "import": import_str,
                    "expected_path": expected,
                    "exists": False,
                })

    return broken


def _check_js_file(fpath: Path, root: Path) -> list[dict[str, Any]]:
    """Check relative imports in a JavaScript file."""
    broken = []
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    file_dir = fpath.parent
    candidates: list[str] = []

    for m in _JS_IMPORT_FROM_RE.finditer(content):
        candidates.append(m.group(1))
    for m in _JS_REQUIRE_RE.finditer(content):
        candidates.append(m.group(1))

    for import_path in candidates:
        clean = import_path.split("?")[0].split("#")[0]
        candidate = (file_dir / clean).resolve()

        extensions = ["", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"]
        found = any(
            Path(str(candidate) + ext).exists() if ext and not ext.startswith("/")
            else (candidate / ext.lstrip("/")).exists() if ext.startswith("/")
            else candidate.exists()
            for ext in extensions
        )

        if not found:
            try:
                rel = str(fpath.relative_to(root))
                expected = str(candidate.relative_to(root))
            except ValueError:
                rel = str(fpath)
                expected = str(candidate)
            broken.append({
                "file": rel,
                "import": import_path,
                "expected_path": expected,
                "exists": False,
            })

    return broken
