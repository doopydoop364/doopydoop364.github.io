#!/usr/bin/env bash
# Arch64 provisioning for the user's CachyOS host; keeps Arch86 intact.
set -Eeuo pipefail
[[ ${EUID} == 0 ]] || { echo "Run with sudo: sudo bash arch64-install.sh" >&2; exit 1; }
HOST_USER="${SUDO_USER:-iuselinuxbtw}"
HOST_HOME="$(getent passwd "$HOST_USER" | cut -d: -f6)"
SSD="/mnt/512GB SSD"
OLD="$SSD/Arch86/remote.img"
NEW_DIR="$SSD/Arch64"
DISK="$NEW_DIR/arch64.img"
ROOT="$(mktemp -d /mnt/arch64-install.XXXXXX)"
OLD_ROOT="$(mktemp -d /mnt/arch86-read.XXXXXX)"
LOOP="" OLD_LOOP=""
MOUNTED=0 OLD_MOUNTED=0
cleanup() {
  set +e
  if [[ "$MOUNTED" == 1 ]]; then umount -R "$ROOT"; fi
  if [[ "$OLD_MOUNTED" == 1 ]]; then umount "$OLD_ROOT"; fi
  [[ -n "$LOOP" ]] && losetup -d "$LOOP"
  [[ -n "$OLD_LOOP" ]] && losetup -d "$OLD_LOOP"
  rmdir "$ROOT" "$OLD_ROOT" 2>/dev/null || true
}
trap cleanup EXIT
echo "Arch64 installer: creates ONLY $DISK, preserves Arch86."
mountpoint -q "$SSD" || { echo "SSD isn't mounted. Refusing to continue." >&2; exit 1; }
[[ -f "$OLD" ]] || { echo "Arch86 source disk not found at $OLD" >&2; exit 1; }
[[ ! -e "$DISK" ]] || { echo "Arch64 disk already exists; refusing overwrite: $DISK" >&2; exit 1; }
command -v arch-chroot >/dev/null || { echo "Install arch-install-scripts first." >&2; exit 1; }
for c in qemu-img losetup sfdisk mkfs.ext4 pacstrap genfstab grub-install; do
  command -v "$c" >/dev/null || { echo "Missing host command: $c" >&2; exit 1; }
done
if systemctl is-active --quiet arch86-vm; then
  echo "Arch86 is running. Shut it down before copying its migration archive." >&2
  exit 1
fi
if fuser -s "$OLD" 2>/dev/null; then echo "Arch86 disk is busy. Stop its QEMU instance." >&2; exit 1; fi
df -h "$SSD"
read -r -p "Name for the new Arch64 login [eli]: " LOGIN
LOGIN="${LOGIN:-eli}"
[[ "$LOGIN" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid Linux username" >&2; exit 1; }
read -r -s -p "New Arch64 account password: " PW; echo
read -r -s -p "Confirm password: " PW2; echo
[[ -n "$PW" && "$PW" == "$PW2" ]] || { echo "Passwords differ or empty." >&2; exit 1; }
unset PW2
read -r -p "Create the separate 64 GiB disk and install Arch64 now? Type YES: " ANSWER
[[ "$ANSWER" == YES ]] || exit 0
mkdir -p "$NEW_DIR"
qemu-img create -f raw "$DISK" 64G
chown "$HOST_USER:$HOST_USER" "$DISK"
LOOP=$(losetup --find --show --partscan "$DISK")
printf 'label: dos\nstart=2048, type=83, bootable\n' | sfdisk "$LOOP"
partprobe "$LOOP" || true
udevadm settle
PART="${LOOP}p1"
[[ -b "$PART" ]] || { echo "Partition node missing: $PART" >&2; exit 1; }
mkfs.ext4 -F -L ARCH64 "$PART"
mount "$PART" "$ROOT"; MOUNTED=1
PAC="$(mktemp)"
cat > "$PAC" <<'PACMAN'
[options]
Architecture = x86_64
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
CheckSpace
[core]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[extra]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[multilib]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
PACMAN
mkdir -p "$ROOT/etc/pacman.d"
printf 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n' > "$ROOT/etc/pacman.d/mirrorlist"
echo "Installing official Arch Linux x86_64 system..."
pacstrap -C "$PAC" -K -M "$ROOT" base linux linux-firmware grub sudo openssh networkmanager vim nano fish git curl wget rsync tmux neovim gcc clang gdb python base-devel xorg-server xorg-xinit i3-wm i3status dmenu xterm
rm -f "$PAC"
genfstab -U "$ROOT" >> "$ROOT/etc/fstab"
echo arch64 > "$ROOT/etc/hostname"
ln -sf /usr/share/zoneinfo/America/New_York "$ROOT/etc/localtime"
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' "$ROOT/etc/locale.gen"
arch-chroot "$ROOT" locale-gen
echo LANG=en_US.UTF-8 > "$ROOT/etc/locale.conf"
arch-chroot "$ROOT" useradd -m -G wheel -s /usr/bin/fish "$LOGIN"
printf '%s:%s\n' "$LOGIN" "$PW" | arch-chroot "$ROOT" chpasswd
unset PW
mkdir -p "$ROOT/etc/sudoers.d"
printf '%%wheel ALL=(ALL:ALL) ALL\n' > "$ROOT/etc/sudoers.d/10-wheel"
chmod 440 "$ROOT/etc/sudoers.d/10-wheel"
arch-chroot "$ROOT" systemctl enable NetworkManager sshd
arch-chroot "$ROOT" mkinitcpio -P
echo "Installing BIOS GRUB bootloader into NEW image only..."
arch-chroot "$ROOT" grub-install --target=i386-pc --recheck "$LOOP"
arch-chroot "$ROOT" grub-mkconfig -o /boot/grub/grub.cfg
echo "Reading ONLY exported migration files from Arch86 (read-only mount)..."
OLD_LOOP=$(losetup --read-only --find --show --partscan "$OLD")
udevadm settle
mount -o ro,noload "${OLD_LOOP}p1" "$OLD_ROOT"; OLD_MOUNTED=1
EXPORT="$(find "$OLD_ROOT/home" -maxdepth 3 -path '*/arch86-migration/home-selected.tar.gz' -type f -print -quit)"
if [[ -n "$EXPORT" ]]; then
  mkdir -p "$ROOT/home/$LOGIN/arch86-migration"
  cp "$EXPORT" "$ROOT/home/$LOGIN/arch86-migration/home-selected.tar.gz"
  for name in packages-repo.txt packages-foreign.txt; do
    find "$OLD_ROOT/home" -maxdepth 3 -path "*/arch86-migration/$name" -type f -print -quit | while read -r src; do cp "$src" "$ROOT/home/$LOGIN/arch86-migration/$name"; done
  done
  chown -R "1000:1000" "$ROOT/home/$LOGIN/arch86-migration"
  echo "Migration archive copied to new guest home; restore after first boot."
else
  echo "No archive detected: can migrate later by transferring ~/arch86-migration manually."
fi
mkdir -p "$ROOT/usr/local/bin"
cat > "$ROOT/usr/local/bin/arch64-onboard" <<'ONBOARD'
#!/usr/bin/env bash
set -euo pipefail
cd "$HOME"
if [[ -f arch86-migration/home-selected.tar.gz ]]; then
  echo "Review archive before restore (may contain private data):"
  tar -tzf arch86-migration/home-selected.tar.gz | head -n 25 || true
  read -r -p "Restore selected Arch86 settings/projects? [y/N] " ok
  if [[ "$ok" == y ]]; then
    tar -xzf arch86-migration/home-selected.tar.gz -C "$HOME" --no-same-owner
  fi
fi
sudo pacman -Syu --needed git openssh curl rsync neovim tmux fish starship fastfetch btop ripgrep fd fzf bat eza lazygit jq cmake python nodejs rustup tailscale
sudo systemctl enable --now tailscaled
echo "Now authenticate Tailscale interactively (use unique Arch64 device identity):"
sudo tailscale up --hostname=arch64
echo "Now authorize GitHub CLI using your browser/device flow:"
gh auth login || true
echo "GitHub SSH: ssh-keygen -t ed25519; register public key in GitHub."
echo "Existing Arch86 private SSH key and Tailscale state were NOT copied."
ONBOARD
chmod 755 "$ROOT/usr/local/bin/arch64-onboard"
# gh included in onboard prerequisites
sed -i 's/ tailscale$/ tailscale github-cli/' "$ROOT/usr/local/bin/arch64-onboard"
echo "Arch64 image installed. Creating separate systemd services..."
cat > /etc/systemd/system/arch64-vm.service <<'UNIT'
[Unit]
Description=Arch64 QEMU/KVM Dev VM
RequiresMountsFor="/mnt/512GB SSD"
After=network.target
[Service]
Type=simple
User=iuselinuxbtw
WorkingDirectory=/mnt
ExecStart=/usr/bin/qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 4096 -machine pc -drive "file=/mnt/512GB SSD/Arch64/arch64.img,format=raw,if=ide" -boot order=c -nic user,model=e1000 -display none -vnc 127.0.0.1:3 -monitor "unix:/mnt/512GB SSD/Arch64/monitor.sock,server=on,wait=off"
ExecStop=/usr/local/bin/arch64-stop $MAINPID
Restart=on-failure
RestartSec=10
TimeoutStopSec=105
[Install]
WantedBy=multi-user.target
UNIT
sed -i "s/^User=.*/User=$HOST_USER/" /etc/systemd/system/arch64-vm.service
cat > /usr/local/bin/arch64-stop <<'STOP'
#!/usr/bin/env bash
PID="${1:-}"
SOCKET="/mnt/512GB SSD/Arch64/monitor.sock"
if [[ -S "$SOCKET" ]]; then printf 'system_powerdown\n' | timeout 3 socat -T 1 - "UNIX-CONNECT:$SOCKET" >/dev/null 2>&1 || true; fi
for ((i=0;i<90;i++)); do
  s=$(ps -o stat= -p "$PID" 2>/dev/null || true)
  [[ -z "$s" || "$s" == Z* ]] && exit 0
  sleep 1
done
exit 1
STOP
chmod 755 /usr/local/bin/arch64-stop
cat > /etc/systemd/system/arch64-novnc.service <<'UNIT'
[Unit]
Description=Arch64 local noVNC
Requires=arch64-vm.service
After=arch64-vm.service
[Service]
Type=simple
User=iuselinuxbtw
WorkingDirectory=/home/iuselinuxbtw/noVNC
ExecStart=/home/iuselinuxbtw/noVNC/utils/novnc_proxy --vnc 127.0.0.1:5903 --listen 127.0.0.1:6083
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
sed -i "s/^User=.*/User=$HOST_USER/;s|/home/iuselinuxbtw/noVNC|$HOST_HOME/noVNC|g" /etc/systemd/system/arch64-novnc.service
[[ -x "$HOST_HOME/noVNC/utils/novnc_proxy" ]] || echo "WARNING: noVNC not at $HOST_HOME/noVNC"
systemctl daemon-reload
systemctl enable arch64-vm arch64-novnc
cleanup; trap - EXIT
systemctl start arch64-vm arch64-novnc
cat <<'DONE'
Arch64 installed separately; Arch86 was not modified.
Open http://127.0.0.1:6083/vnc.html on CachyOS.
Log in with the username/password entered earlier, then run:
  arch64-onboard
This sets up user tools, restores selected files with permission, then starts interactive Tailscale/GitHub logins.
Cloudflare remains UNCHANGED. Do not publish this VM before protecting it with Access.
DONE
