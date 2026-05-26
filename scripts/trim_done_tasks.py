#!/usr/bin/env python3
"""Trim detail lines from completed tasks in tasks.md.

For every [x] task, keeps only the header line with essential tags:
  type:, p1/p2/p3, needs:, rollup:
Drops: model:, output:, and all sub-bullet detail lines.

Non-completed tasks ([ ], [~], [!]) and all structure (## headers,
blank lines outside completed-task detail blocks) are untouched.

Usage:
    python3 scripts/trim_done_tasks.py [tasks.md] [--dry-run]

Writes in-place by default. --dry-run prints to stdout only.
A .bak backup is always written before modifying the original.
"""

import re
import sys
from pathlib import Path

# A task header line:  - [x] **T123** title ...tags...
TASK_RE = re.compile(r"^- \[([x~! ])\] ")

# Markdown section headers (## level only — single # can appear in code snippets)
SECTION_RE = re.compile(r"^## ")

# Metadata tags — backtick-wrapped, specific key prefixes
META_TAG_RE = re.compile(
    r"`("
    r"type:[^`]+"       # type:code_generate etc.
    r"|p[1-9]"          # p1, p2, p3
    r"|needs:[^`]+"     # needs:T001,T002
    r"|rollup:[^`]+"    # rollup:T090,T091
    r"|model:[^`]+"     # model:local|claude  (to find position only — dropped)
    r"|output:[^`]+"    # output:FILE.md      (to find position only — dropped)
    r")`"
)

# Tags to KEEP on the trimmed header
KEEP_RE = re.compile(
    r"`("
    r"type:[^`]+"
    r"|p[1-9]"
    r"|needs:[^`]+"
    r"|rollup:[^`]+"
    r")`"
)


def trim_header(line: str) -> str:
    """Return the task header with title intact but only essential tags kept."""
    # Capture: prefix "- [x] **T###** ", then the rest
    m = re.match(r"^(- \[[x]\] \*\*T\d+\*\* )(.*)", line)
    if not m:
        return line  # unexpected format — leave untouched

    prefix = m.group(1)   # "- [x] **T285** "
    rest   = m.group(2)   # title text + inline tags + metadata tags

    # Find position of the first METADATA tag (type:/p1/needs:/model:/output:/rollup:)
    first_meta = META_TAG_RE.search(rest)
    if first_meta is None:
        # No metadata tags found — keep line as-is (nothing to trim)
        return line

    # Title = everything before the first metadata tag, stripped of trailing whitespace
    title = rest[: first_meta.start()].rstrip()

    # Collect kept tags in order
    kept_tags = [match.group() for match in KEEP_RE.finditer(rest)]

    rebuilt = prefix + title
    if kept_tags:
        rebuilt += " " + " ".join(kept_tags)
    return rebuilt + "\n"


def process(lines: list[str]) -> list[str]:
    out: list[str] = []
    skip = False  # True while inside a [x] task's detail block

    for line in lines:
        task_m = TASK_RE.match(line)
        if task_m:
            status = task_m.group(1)
            if status == "x":
                out.append(trim_header(line))
                skip = True
            else:
                out.append(line)
                skip = False
        elif SECTION_RE.match(line):
            # ## section header — always emit, stop skipping
            out.append(line)
            skip = False
        elif skip:
            # Detail line inside a completed task — drop it
            pass
        else:
            out.append(line)

    return out


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]

    path = Path(args[0]) if args else Path(__file__).parent.parent / "tasks.md"

    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    trimmed = process(lines)
    result = "".join(trimmed)

    before = len(lines)
    after  = len(trimmed)
    saved  = before - after

    if dry_run:
        print(result, end="")
        print(f"\n# --- dry-run: {before} → {after} lines (-{saved}) ---",
              file=sys.stderr)
        return

    bak = path.with_suffix(".md.bak")
    bak.write_text(original, encoding="utf-8")
    path.write_text(result, encoding="utf-8")

    print(f"Trimmed {path}: {before} → {after} lines (-{saved}). Backup: {bak}")


if __name__ == "__main__":
    main()
