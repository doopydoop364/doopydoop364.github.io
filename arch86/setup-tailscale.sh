#!/bin/sh
# Arch86: configure a dedicated systemd service for Tailscale userspace mode.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo sh setup-tailscale.sh" >&2
  exit 1
fi
command -v systemctl >/dev/null 2>&1 || { echo "This script requires systemd." >&2; exit 1; }
DAEMON=$(command -v tailscaled || true)
[ -n "$DAEMON" ] || { echo "tailscaled is not installed." >&2; exit 1; }
if pgrep -x tailscaled >/dev/null 2>&1 && ! systemctl is-active --quiet arch86-tailscaled.service; then
  echo "A manually started tailscaled is already running." >&2
  echo "Stop it in tmux (Ctrl+C) and rerun this script. Do not delete its state." >&2
  exit 1
fi
install -d -m 700 /var/lib/tailscale
cat >/etc/systemd/system/arch86-tailscaled.service <<EOF
[Unit]
Description=Arch86 Tailscale (userspace networking)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart=$DAEMON --tun=userspace-networking --socks5-server=127.0.0.1:1055 --state=/var/lib/tailscale/tailscaled.state
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now arch86-tailscaled.service
systemctl --no-pager --full status arch86-tailscaled.service || true
echo "Check connection: tailscale status"
echo "If login is needed: sudo tailscale up --hostname=arch86"
