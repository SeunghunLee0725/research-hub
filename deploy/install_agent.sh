#!/usr/bin/env bash
# Install the node agent on this machine.
# Usage: deploy/install_agent.sh <hub-url> <node-token>
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <hub-url> <node-token>" >&2; exit 2; }
repo="$(cd "$(dirname "$0")/.." && pwd)"
[ "$repo" = "$HOME/research-hub" ] || { echo "clone the repo to ~/research-hub first" >&2; exit 2; }
conf="$HOME/.config/research-hub"
mkdir -p "$conf" "$HOME/.config/systemd/user"
umask 077
printf 'HUB_URL=%s\nHUB_NODE_TOKEN=%s\nAGENT_DISK_PATH=/\n' "$1" "$2" > "$conf/agent.env"
cp "$repo/deploy/systemd/research-hub-agent.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now research-hub-agent.service
systemctl --user --no-pager status research-hub-agent.service | head -5
