#!/usr/bin/env sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugin_name=project-workflow-suite
marketplace_name=project-workflow-suite
marketplace_root="$HOME/.codex/marketplaces/$marketplace_name"
target_root="$marketplace_root/plugins/$plugin_name"
marketplace_path="$marketplace_root/.agents/plugins/marketplace.json"

if ! command -v codebase-memory-mcp >/dev/null 2>&1; then
    echo "codebase-memory-mcp is required." >&2
    echo "Install it from https://github.com/DeusData/codebase-memory-mcp, then run this installer again." >&2
    exit 1
fi

mkdir -p "$target_root"
cp -R "$plugin_root"/. "$target_root"/
find "$target_root" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$target_root" -type f -name '*.pyc' -delete

command -v python3 >/dev/null 2>&1 || {
    echo "python3 is required to create the Codex marketplace manifest" >&2
    exit 1
}
mkdir -p "$(dirname -- "$marketplace_path")"
python3 - "$marketplace_path" "$plugin_name" "$marketplace_name" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
name = sys.argv[2]
marketplace_name = sys.argv[3]
data = {"name": marketplace_name, "interface": {"displayName": "Project Workflow Suite"}, "plugins": []}
entry = {
    "name": name,
    "source": {"source": "local", "path": f"./plugins/{name}"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}
data["plugins"] = [entry]
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY

command -v codex >/dev/null 2>&1 || {
    echo "Codex CLI is required to install the plugin" >&2
    exit 1
}
codex plugin marketplace add "$marketplace_root"
codex plugin add "$plugin_name@$marketplace_name"
codebase-memory-mcp --version
echo "Installation complete. Restart Codex and open a new task."
