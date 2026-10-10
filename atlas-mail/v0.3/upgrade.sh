#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || { echo "Run as root"; exit 1; }
cd /tmp
git clone --branch atlas-mail-temp --single-branch https://github.com/doopydoop364/doopydoop364.github.io.git atlas-mail-update-$$
src="$(find /tmp -maxdepth 1 -type d -name 'atlas-mail-update-*' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)/atlas-mail/v0.3"
test -s "$src/server.py"
python3 -m py_compile "$src/server.py"
cp -a /opt/atlas-mail/server.py "/opt/atlas-mail/server.py.bak.$(date +%Y%m%d%H%M%S)"
install -m 644 "$src/server.py" /opt/atlas-mail/server.py
mkdir -p /etc/systemd/system/atlas-mail.service.d
printf '[Service]\nStateDirectory=atlas-mail\nEnvironment=ATLAS_DB=/var/lib/atlas-mail/drafts.sqlite3\n' > /etc/systemd/system/atlas-mail.service.d/storage.conf
systemctl daemon-reload
systemctl restart atlas-mail
systemctl is-active atlas-mail
echo "Upgrade installed. Inspect journalctl -u atlas-mail if necessary."
