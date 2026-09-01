#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"
port=4173

if ! command -v python3 >/dev/null 2>&1; then
  echo "Gecko requires Python 3. Run setup.command after installing Python."
  read -r "?Press Return to close..."
  exit 1
fi

if /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Gecko is already running at http://127.0.0.1:$port"
  open "http://127.0.0.1:$port"
  exit 0
fi

python3 -c "from gecko_store import store; store.ensure_files()"
open "http://127.0.0.1:$port"
exec python3 gecko_server.py --port "$port"
