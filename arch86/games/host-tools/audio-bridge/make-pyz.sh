#!/bin/sh
# Build a single-file bridge from arch_games/remote (stdlib only). Run from anywhere.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/arch_games"
: > "$tmp/arch_games/__init__.py"
cp -R "$root/arch_games/remote" "$tmp/arch_games/remote"
find "$tmp" -name __pycache__ -prune -exec rm -rf {} +
printf 'from arch_games.remote.__main__ import main\nmain()\n' > "$tmp/__main__.py"
python3 -m zipapp "$tmp" -o "$here/arch-games-audio-bridge.pyz" -p '/usr/bin/env python3'
echo "Built $here/arch-games-audio-bridge.pyz"
