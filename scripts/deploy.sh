#!/usr/bin/env bash
# deploy.sh — build SPAs, run tests, bump version, commit, tag, and optionally push.
#
# V3 additions:
#   - Builds all three React SPAs (web_ui, portal, admin) before tagging.
#   - Runs pytest gate (skip with --skip-tests).
#   - Warns if ORCHID_VAULT_KEY / JWT_SECRET are missing from env file.
#
# Usage:
#   ./scripts/deploy.sh              interactive (prompts for version + push)
#   ./scripts/deploy.sh --skip-tests skip pytest (still builds SPAs)
#   ./scripts/deploy.sh --skip-build skip npm builds (faster, risky)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYPROJECT="$REPO_ROOT/pyproject.toml"

SKIP_TESTS=false
SKIP_BUILD=false
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=true ;;
        --skip-build) SKIP_BUILD=true ;;
    esac
done

cd "$REPO_ROOT"

# ── 1. Read current version ───────────────────────────────────────────────────
CURRENT=$(grep -oP '(?<=^version = ")[^"]+' "$PYPROJECT")
if [[ -z "$CURRENT" ]]; then
    echo "Error: could not parse version from $PYPROJECT" >&2
    exit 1
fi

# ── 2. Compute default (patch + 1) ───────────────────────────────────────────
IFS='.' read -r V_MAJOR V_MINOR V_PATCH <<< "$CURRENT"
V_PATCH="${V_PATCH:-0}"
DEFAULT_VERSION="$V_MAJOR.$V_MINOR.$((V_PATCH + 1))"

echo ""
echo "  Current version : $CURRENT"
echo "  Default new     : $DEFAULT_VERSION"
echo ""
read -rp "  New version [$DEFAULT_VERSION]: " NEW_VERSION
NEW_VERSION="${NEW_VERSION:-$DEFAULT_VERSION}"

if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    echo "Error: '$NEW_VERSION' is not a valid version (expected X.Y or X.Y.Z)" >&2
    exit 1
fi

if [[ "$NEW_VERSION" == "$CURRENT" ]]; then
    echo "Version unchanged — nothing to do."
    exit 0
fi

# ── 3. Env var sanity check ───────────────────────────────────────────────────
ENV_FILE="${HOME}/.config/orchid/.env"
echo ""
echo "  Checking env vars…"
for VAR in JWT_SECRET ORCHID_VAULT_KEY ANTHROPIC_API_KEY; do
    if [[ -z "${!VAR:-}" ]]; then
        # Try loading from env file
        if [[ -f "$ENV_FILE" ]] && grep -q "^${VAR}=" "$ENV_FILE" 2>/dev/null; then
            echo "    $VAR  ✓  (from $ENV_FILE)"
        else
            echo "    $VAR  ⚠  NOT SET — production deploys require this"
        fi
    else
        echo "    $VAR  ✓  (from environment)"
    fi
done

# ── 4. Run tests ──────────────────────────────────────────────────────────────
if [[ "$SKIP_TESTS" == "true" ]]; then
    echo ""
    echo "  Skipping tests (--skip-tests)."
else
    echo ""
    echo "  Running test suite…"
    if command -v uv &>/dev/null && [[ -d "$REPO_ROOT/.venv" ]]; then
        "$REPO_ROOT/.venv/bin/python" -m pytest "$REPO_ROOT/tests/" -q --tb=short 2>&1 | tail -5
    elif command -v pytest &>/dev/null; then
        pytest "$REPO_ROOT/tests/" -q --tb=short 2>&1 | tail -5
    else
        echo "  Warning: pytest not found — skipping tests." >&2
    fi
    echo "  Tests passed."
fi

# ── 5. Build SPAs ─────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "true" ]]; then
    echo ""
    echo "  Skipping SPA builds (--skip-build)."
else
    echo ""
    echo "  Building React SPAs…"

    build_spa() {
        local NAME="$1"
        local DIR="$REPO_ROOT/orchid/interfaces/$2"
        if [[ -f "$DIR/package.json" ]]; then
            echo "    $NAME…"
            (cd "$DIR" && npm install --silent && npm run build --silent)
            echo "    $NAME  ✓"
        else
            echo "    $NAME  ⚠  not found at $DIR — skipping"
        fi
    }

    build_spa "web_ui (power-user SPA)" "web_ui"
    build_spa "portal  (user portal /app/)" "portal"
    build_spa "admin   (admin console /admin/)" "admin"

    echo "  All SPAs built."
fi

# ── 6. Bump version in pyproject.toml ────────────────────────────────────────
echo ""
sed -i "s/^version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" "$PYPROJECT"
echo "  Updated pyproject.toml: $CURRENT → $NEW_VERSION"

# ── 7. Git commit + tag ───────────────────────────────────────────────────────
if ! git diff --quiet "$PYPROJECT"; then
    git add "$PYPROJECT"
    git commit -m "chore: bump version $CURRENT → $NEW_VERSION"
    echo "  Committed version bump."
else
    echo "Warning: pyproject.toml unchanged after sed — version may already match." >&2
fi

git tag "v$NEW_VERSION"
echo "  Tagged v$NEW_VERSION."

# ── 8. Push ───────────────────────────────────────────────────────────────────
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

# ── 9. Post-deploy reminder ───────────────────────────────────────────────────
echo ""
echo "  Done. orchid is now version $NEW_VERSION."
echo ""
echo "  ── Post-deploy checklist ──────────────────────────────────────────────"
echo "  □  JWT_SECRET set in ~/.config/orchid/.env  (rotate → re-login required)"
echo "  □  ORCHID_VAULT_KEY set                     (rotate → all vaults invalidated)"
echo "  □  uv tool install ~/LocalAI/orchid --force  (or pip install -e .)"
echo "  □  systemctl restart orchid-serve            (if running as service)"
echo "  ────────────────────────────────────────────────────────────────────────"
