---
name: Use CODE_INDEX before reading files
description: Always consult memory/CODE_INDEX.md to locate code before opening files; update it after any code changes
type: feedback
---

Always check `memory/CODE_INDEX.md` first to locate classes/functions before reading source files.

**Why:** User wants a persistent navigation index to avoid re-reading all code files every session.

**How to apply:** Before grepping or reading source, look up the relevant module in CODE_INDEX.md. After adding, removing, or renaming any class/function, update CODE_INDEX.md to reflect the change.
