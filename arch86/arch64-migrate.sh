#!/usr/bin/env bash
set -euo pipefail
if (( EUID == 0 )); then echo 'Run as your regular user, not root.' >&2; exit 1; fi
MODE="${1:-help}"; shift || true
if [[ "$MODE" == export ]]; then
  OUT="${1:-$HOME/arch86-migration}"
  mkdir -p "$OUT"
  pacman -Qqen > "$OUT/packages-repo.txt"
  pacman -Qqem > "$OUT/packages-foreign.txt" || true
  ITEMS=()
  for f in .config/fish .config/nvim .config/i3 .tmux.conf .config/starship.toml Projects Scripts Scratch; do
    [[ -e "$HOME/$f" ]] && ITEMS+=("$f")
  done
  if (( ${#ITEMS[@]} )); then
    tar -C "$HOME" -czf "$OUT/home-selected.tar.gz" --exclude='*/.env' --exclude='*/.env.*' --exclude='*/.venv' --exclude='*/node_modules' --exclude='*/.ssh' --exclude='*/id_*' --exclude='*/tailscaled.state' "${ITEMS[@]}"
    chmod 600 "$OUT/home-selected.tar.gz"
  fi
  echo "Export at $OUT; it may contain sensitive project data. Keep private."
  echo "Original VM is unchanged."
elif [[ "$MODE" == setup ]]; then
  [[ "$(uname -m)" == x86_64 ]] || { echo 'Run setup on NEW 64-bit Arch, not Arch86.' >&2; exit 1; }
  sudo pacman -Sy
  PKGS=(base-devel git openssh rsync curl wget nano neovim vim tmux fish starship fastfetch btop htop jq ripgrep fd fzf bat eza lazygit gcc clang llvm gdb cmake python python-pip python-virtualenv nodejs npm rustup unzip zip tar zstd less tree which xorg-server xorg-xinit i3-wm i3status dmenu xterm)
  AVAILABLE=()
  for p in "${PKGS[@]}"; do
    if pacman -Si "$p" >/dev/null 2>&1; then AVAILABLE+=("$p"); else echo "Unavailable, skipped: $p"; fi
  done
  sudo pacman -Syu --needed "${AVAILABLE[@]}"
  mkdir -p "$HOME/Projects" "$HOME/Scripts" "$HOME/Scratch"
  if [[ -n "${1:-}" ]]; then
    [[ -f "$1" ]] || { echo "No archive: $1" >&2; exit 1; }
    tar -tzf "$1" | head -n 30 || true
    read -r -p 'Restore selected home files, possibly overwriting? [y/N] ' ok
    if [[ "$ok" == y ]]; then tar -xzf "$1" -C "$HOME" --no-same-owner; fi
  fi
  echo 'Next: Set up new Tailscale identity, GitHub SSH key, and validate Xorg/i3.'
  echo 'Do NOT copy /etc, /usr, private SSH keys, Tailscale state, or machine-id from Arch86.'
elif [[ "$MODE" == packages ]]; then
  FILE="${1:?Pass old packages-repo.txt}"
  PKGS=()
  while IFS= read -r p; do
    [[ "$p" =~ ^[a-zA-Z0-9@._+-]+$ ]] || continue
    if pacman -Si "$p" >/dev/null 2>&1; then PKGS+=("$p"); else echo "Unavailable: $p"; fi
  done < "$FILE"
  echo "Available packages: ${#PKGS[@]}"
  read -r -p 'Install available old packages? [y/N] ' ok
  if [[ "$ok" == y ]] && (( ${#PKGS[@]} )); then sudo pacman -S --needed "${PKGS[@]}"; fi
else
  echo 'Usage: bash arch64-migrate.sh export [directory]'
  echo '       bash arch64-migrate.sh setup [home-selected.tar.gz]'
  echo '       bash arch64-migrate.sh packages packages-repo.txt'
fi
