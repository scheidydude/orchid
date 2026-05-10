#!/usr/bin/env bash
# deploy.sh — bump version in pyproject.toml, commit, tag, and optionally push.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYPROJECT="$REPO_ROOT/pyproject.toml"

# ── read current version ──────────────────────────────────────────────────────
CURRENT=$(grep -oP '(?<=^version = ")[^"]+' "$PYPROJECT")
if [[ -z "$CURRENT" ]]; then
    echo "Error: could not parse version from $PYPROJECT" >&2
    exit 1
fi

# ── compute default (patch + 1) ──────────────────────────────────────────────
IFS='.' read -r V_MAJOR V_MINOR V_PATCH <<< "$CURRENT"
V_PATCH="${V_PATCH:-0}"
DEFAULT_VERSION="$V_MAJOR.$V_MINOR.$((V_PATCH + 1))"

# ── prompt ───────────────────────────────────────────────────────────────────
echo ""
echo "  Current version : $CURRENT"
echo "  Default new     : $DEFAULT_VERSION"
echo ""
read -rp "  New version [$DEFAULT_VERSION]: " NEW_VERSION
NEW_VERSION="${NEW_VERSION:-$DEFAULT_VERSION}"

# ── validate ─────────────────────────────────────────────────────────────────
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    echo "Error: '$NEW_VERSION' is not a valid version (expected X.Y or X.Y.Z)" >&2
    exit 1
fi

if [[ "$NEW_VERSION" == "$CURRENT" ]]; then
    echo "Version unchanged — nothing to do."
    exit 0
fi

# ── update pyproject.toml ────────────────────────────────────────────────────
sed -i "s/^version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" "$PYPROJECT"
echo ""
echo "  Updated pyproject.toml: $CURRENT → $NEW_VERSION"

# ── git commit + tag ─────────────────────────────────────────────────────────
cd "$REPO_ROOT"

if ! git diff --quiet "$PYPROJECT"; then
    git add "$PYPROJECT"
    git commit -m "chore: bump version $CURRENT → $NEW_VERSION"
    echo "  Committed version bump."
else
    echo "Warning: pyproject.toml unchanged after sed — version may already match." >&2
fi

git tag "v$NEW_VERSION"
echo "  Tagged v$NEW_VERSION."

# ── push ─────────────────────────────────────────────────────────────────────
echo ""
read -rp "  Push to origin (main + tag)? [y/N]: " PUSH_CONFIRM
if [[ "${PUSH_CONFIRM,,}" == "y" ]]; then
    git push origin main
    git push origin "v$NEW_VERSION"
    echo "  Pushed main and tag v$NEW_VERSION."
else
    echo "  Skipped push. Run manually:"
    echo "    git push origin main && git push origin v$NEW_VERSION"
fi

echo ""
echo "  Done. orchid is now version $NEW_VERSION."
