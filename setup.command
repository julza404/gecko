#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Gecko requires Python 3. Install it from https://www.python.org/downloads/"
  read -r "?Press Return to close..."
  exit 1
fi

python3 -m py_compile gecko_store.py gecko_agent.py gecko_server.py gecko_afk_sync.py
python3 -c "from gecko_store import store; store.ensure_files()"

echo "Gecko is ready."
echo "Open start.command to launch it."
read -r "?Press Return to close..."
