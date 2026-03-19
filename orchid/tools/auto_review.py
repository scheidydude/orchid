"""Auto-review tool — runs check_imports and syntax verification on a set of files."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from orchid.tools.consistency import check_imports

logger = logging.getLogger(__name__)


def run_auto_review(project_path: Path, files: list[str]) -> dict[str, Any]:
    """
    Run check_imports and per-file syntax verification on the given files.

    Returns a dict with:
      - broken_imports: list of broken import dicts from check_imports
      - syntax_errors: list of {file, error} for files with syntax problems
      - files_checked: number of files checked
      - passed: bool — True if no issues found
      - summary: human-readable summary string
    """
    root = Path(project_path).resolve()

    # ── 1. Import consistency check ──────────────────────────────────────────
    broken_imports = check_imports(project_path)

    # Filter to only imports from the files we care about (if files list given)
    if files:
        rel_files = set()
        for f in files:
            p = Path(f)
            if p.is_absolute():
                try:
                    rel_files.add(str(p.relative_to(root)))
                except ValueError:
                    rel_files.add(str(p))
            else:
                rel_files.add(f)
        broken_imports = [b for b in broken_imports if b["file"] in rel_files]

    # ── 2. Per-file syntax verification ─────────────────────────────────────
    syntax_errors: list[dict[str, str]] = []

    target_files = files if files else []
    if not target_files:
        # Fall back to all .py and .js in project
        for ext in ("*.py", "*.js"):
            for p in root.rglob(ext):
                if not any(part in p.parts for part in
                           ("node_modules", ".venv", "dist", "build", "__pycache__", ".git")):
                    target_files.append(str(p))

    for file_path in target_files:
        p = Path(file_path)
        if not p.is_absolute():
            p = root / file_path
        if not p.exists():
            continue

        suffix = p.suffix.lower()
        error = _check_syntax(p, suffix)
        if error:
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            syntax_errors.append({"file": rel, "error": error})

    # ── 3. Build summary ─────────────────────────────────────────────────────
    files_checked = len(set(target_files))
    passed = not broken_imports and not syntax_errors

    lines = [f"Auto-review checked {files_checked} file(s)."]
    if passed:
        lines.append("✓ No issues found — all imports resolve and syntax is valid.")
    else:
        if syntax_errors:
            lines.append(f"\n✗ Syntax errors ({len(syntax_errors)}):")
            for se in syntax_errors:
                lines.append(f"  {se['file']}: {se['error']}")
        if broken_imports:
            lines.append(f"\n✗ Broken imports ({len(broken_imports)}):")
            for bi in broken_imports:
                lines.append(f"  {bi['file']}: import '{bi['import']}' → {bi['expected_path']} (not found)")

    summary = "\n".join(lines)
    logger.info("Auto-review complete: passed=%s, syntax_errors=%d, broken_imports=%d",
                passed, len(syntax_errors), len(broken_imports))

    return {
        "broken_imports": broken_imports,
        "syntax_errors": syntax_errors,
        "files_checked": files_checked,
        "passed": passed,
        "summary": summary,
    }


def _check_syntax(p: Path, suffix: str) -> str | None:
    """Return error string if syntax check fails, else None."""
    if suffix == ".py":
        try:
            result = subprocess.run(
                ["python3", "-m", "py_compile", str(p)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return result.stderr.strip() or "syntax error"
        except Exception as e:
            return f"verification failed: {e}"

    elif suffix == ".js":
        try:
            result = subprocess.run(
                ["node", "--input-type=module", "--eval", f"import('{p}')"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout).strip()
                return err or "syntax error"
        except Exception as e:
            return f"verification failed: {e}"

    return None