#!/bin/sh
set -eu
if [ "$(id -u)" -eq 0 ]; then echo 'Run as your regular Arch86 user, not root' >&2; exit 1; fi
command -v python3 >/dev/null || { echo 'Python 3 is required'; exit 1; }
command -v curl >/dev/null || { echo 'curl is required'; exit 1; }
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
echo 'Arch86 Games installed!'
echo 'Fish: fish_add_path ~/.local/bin'
echo 'Terminal: arch86-games'
echo 'Visual catalog: arch86-games web (open localhost:8765 inside Arch86 graphical browser)'
