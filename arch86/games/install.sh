#!/bin/sh
set -eu
if [ "$(id -u)" -eq 0 ]; then echo 'Run as your ordinary VM user, not root.' >&2; exit 1; fi
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl is required.' >&2; exit 1; }
command -v pacman >/dev/null || { echo 'Arch Linux with pacman is required.' >&2; exit 1; }
BASE=https://raw.githubusercontent.com/doopydoop364/doopydoop364.github.io/arch86-tailscale-autostart/arch86/games
DEST="$HOME/.local/share/arch86-games"
mkdir -p "$DEST" "$HOME/.local/bin"
for f in arch86-games.py catalog.txt; do
  curl -fsSL --retry 3 "$BASE/$f" -o "$DEST/$f.tmp"
  mv "$DEST/$f.tmp" "$DEST/$f"
done
cat > "$HOME/.local/bin/arch86-games" <<EOF
#!/bin/sh
exec python3 "$DEST/arch86-games.py" "\$@"
EOF
chmod 755 "$HOME/.local/bin/arch86-games"
ln -sf arch86-games "$HOME/.local/bin/arch64-games"
ln -sf arch86-games "$HOME/.local/bin/arch-games"
echo "Arch Games installed for $(uname -m)."
echo 'If needed in Fish: fish_add_path ~/.local/bin'
echo 'Launch: arch-games (or arch86-games / arch64-games)'
echo 'Gallery: arch-games web — open http://127.0.0.1:8765 in a browser INSIDE the VM.'
echo 'Games not found in your Pacman repositories are hidden.'
