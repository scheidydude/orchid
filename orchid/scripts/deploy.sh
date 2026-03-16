#!/bin/bash
#
# Orchid Deploy Script
# ====================
#
# Usage: ./deploy.sh
#
# This script performs a one-command deployment of Orchid:
#   1. Builds the React frontend
#   2. Reinstalls orchid globally via uv
#   3. Restarts the orchid-serve systemd service
#   4. Tails logs for 5 seconds to confirm clean startup
#
# Requirements:
#   - Node.js and npm installed
#   - uv package manager installed
#   - systemd service 'orchid-serve' configured
#   - sudo privileges for systemd operations
#
# Run from the orchid repository root directory.
#

set -e

echo "🚀 Starting Orchid deployment..."
echo ""

# Step 1: Build React frontend
echo "📦 Building React frontend..."
cd orchid/interfaces/web_ui
npm run build
if [ $? -eq 0 ]; then
    echo "✅ Frontend build completed successfully"
else
    echo "❌ Frontend build failed"
    exit 1
fi
cd ../../..
echo ""

# Step 2: Reinstall orchid globally
echo "🔧 Reinstalling orchid globally..."
uv tool install . --force
if [ $? -eq 0 ]; then
    echo "✅ Orchid reinstalled successfully"
else
    echo "❌ Orchid installation failed"
    exit 1
fi
echo ""

# Step 3: Restart systemd service
echo "🔄 Restarting orchid-serve service..."
sudo systemctl restart orchid-serve
if [ $? -eq 0 ]; then
    echo "✅ Service restarted successfully"
else
    echo "❌ Service restart failed"
    exit 1
fi
echo ""

# Step 4: Tail logs for 5 seconds
echo "📋 Tailing logs for 5 seconds..."
sudo journalctl -u orchid-serve -n 20 --no-pager
echo ""
echo "⏱️  Waiting 5 seconds..."
sleep 5
echo ""
echo "✅ Deployment complete!"
echo ""
echo "To check service status: systemctl status orchid-serve"
echo "To view live logs: journalctl -u orchid-serve -f"
