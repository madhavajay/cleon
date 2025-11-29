#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Setup venv if needed
uv venv -p 3.13 --allow-existing
echo "Installing jupyter dependencies..."
uv pip install jupyter ipykernel maturin

# Activate venv
source .venv/bin/activate

# Set dev mode to suppress extension install hints
export CLEON_DEV_MODE=1

# Ensure venv has pip
python -m ensurepip --upgrade 2>/dev/null || true

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}=== Cleon JupyterLab Dev Mode ===${NC}"
echo ""

# Install cleon and extension in editable mode
echo -e "${BLUE}Installing cleon in editable mode...${NC}"
python -m pip install -e ./python

echo -e "${BLUE}Installing cleon_cell_control in editable mode...${NC}"
python -m pip install -e ./extension

# Function to cleanup background processes on exit
cleanup() {
    echo ""
    echo -e "${BLUE}Shutting down...${NC}"
    kill $WATCH_PID 2>/dev/null || true
    kill $JUPYTER_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start TypeScript watcher in background
echo -e "${BLUE}Starting TypeScript watcher...${NC}"
cd extension
npm run watch 2>&1 | sed 's/^/[watcher] /' &
WATCH_PID=$!
cd "$SCRIPT_DIR"

# Give watcher a moment to start
sleep 2

# Start JupyterLab with autoreload
echo -e "${BLUE}Starting JupyterLab with autoreload...${NC}"
echo ""
python -m cleon jupyter lab --autoreload &
JUPYTER_PID=$!

# Wait for either process to exit
wait $JUPYTER_PID
cleanup
