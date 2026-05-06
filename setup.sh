#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .

mkdir -p "$HOME/.chatterminal/downloads" "$HOME/.chatterminal_server/files"

cat <<'MSG'
ChatterTerminal setup complete.

Run the server:
  . .venv/bin/activate
  ct-server

Run a client:
  . .venv/bin/activate
  ct-client
MSG
