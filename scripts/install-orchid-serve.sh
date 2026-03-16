#!/bin/bash
# Install orchid-serve systemd service
#
# Usage:
#   ./install-orchid-serve.sh
#   ./install-orchid-serve.sh --watch-dir ~/LocalAI --watch-dir ~/Documents/Development
#
# The service file is installed as-is from scripts/orchid-serve.service.
# Pass --watch-dir flags to customise the ExecStart line before installing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="$SCRIPT_DIR/orchid-serve.service"
SERVICE_DEST="/etc/systemd/system/orchid-serve.service"
ORCHID_BIN="/home/dave/.local/bin/orchid"
ORCHID_DIR="/home/dave/LocalAI/orchid"

# Parse --watch-dir arguments
WATCH_DIRS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --watch-dir)
            WATCH_DIRS+=("$2")
            shift 2
            ;;
        --watch-dir=*)
            WATCH_DIRS+=("${1#*=}")
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# Build ExecStart line
EXEC_START="$ORCHID_BIN serve"
if [[ ${#WATCH_DIRS[@]} -gt 0 ]]; then
    for dir in "${WATCH_DIRS[@]}"; do
        EXEC_START+=" \\\n    --watch-dir $dir"
    done
    EXEC_START+=" \\\n    --port 7842"
else
    EXEC_START+=" \\\n    --watch-dir /home/dave/LocalAI \\\n    --port 7842"
fi

if [[ ${#WATCH_DIRS[@]} -gt 0 ]]; then
    # Generate service file with custom watch dirs
    TMP_SERVICE=$(mktemp)
    sed "s|ExecStart=.*|ExecStart=$(printf '%b' "$EXEC_START")|" "$SERVICE_SRC" > "$TMP_SERVICE"
    sudo cp "$TMP_SERVICE" "$SERVICE_DEST"
    rm -f "$TMP_SERVICE"
else
    sudo cp "$SERVICE_SRC" "$SERVICE_DEST"
fi

sudo systemctl daemon-reload
sudo systemctl enable orchid-serve
sudo systemctl start orchid-serve

echo ""
echo "Orchid serve service installed and started."
echo ""
echo "Status:  sudo systemctl status orchid-serve"
echo "Logs:    sudo journalctl -u orchid-serve -f"
echo "Stop:    sudo systemctl stop orchid-serve"
echo "Disable: sudo systemctl disable orchid-serve"
