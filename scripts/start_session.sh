#!/usr/bin/env bash
# Orchid tmux session launcher
# Usage: ./scripts/start_session.sh [project_dir] [session_name]
#
# Creates a tmux session with:
#   - Window 0: orchid interactive chat
#   - Window 1: shell in the project dir
#   - Window 2: log tail

set -euo pipefail

PROJECT="${1:-.}"
SESSION="${2:-orchid}"
ORCHID_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Source venv if it exists
if [[ -f "$ORCHID_DIR/.venv/bin/activate" ]]; then
    ACTIVATE="source $ORCHID_DIR/.venv/bin/activate"
else
    ACTIVATE="echo '[warn] No .venv found — using system Python'"
fi

# Kill existing session if present
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n "chat" -c "$ORCHID_DIR"

# Window 0: interactive chat
tmux send-keys -t "$SESSION:chat" "$ACTIVATE && orchid chat $PROJECT" Enter

# Window 1: shell
tmux new-window -t "$SESSION" -n "shell" -c "$PROJECT"
tmux send-keys -t "$SESSION:shell" "$ACTIVATE" Enter

# Window 2: logs
LOG_DIR="$PROJECT/.orchid/session_logs"
mkdir -p "$LOG_DIR"
tmux new-window -t "$SESSION" -n "logs" -c "$ORCHID_DIR"
tmux send-keys -t "$SESSION:logs" "tail -F $LOG_DIR/*.jsonl 2>/dev/null || echo 'Waiting for logs...'" Enter

# Focus chat window
tmux select-window -t "$SESSION:chat"
tmux attach-session -t "$SESSION"
