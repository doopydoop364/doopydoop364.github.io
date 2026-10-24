#!/bin/sh
# Arch86 i686: install private libxml2 2.13.9 runtime for clangd 15.
# Does not replace system libxml2 or modify /usr/lib.
set -eu
if [ "$(id -u)" -eq 0 ]; then echo "Run as normal user, not root." >&2; exit 1; fi
command -v gcc >/dev/null && command -v make >/dev/null && command -v curl >/dev/null && command -v sha256sum >/dev/null || {
 echo "Missing build tool. Install with: sudo pacman -S --needed base-devel curl" >&2; exit 1;
}
VER=2.13.9
SRC="https://download.gnome.org/sources/libxml2/2.13/libxml2-$VER.tar.xz"
SHA=a2c9ae7b770da34860050c309f903221c67830c86e4a7e760692b803df95143a
PREFIX="$HOME/.local/opt/arch86-libxml2-2.13"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
cd "$WORK"
echo "Downloading official libxml2 $VER source..."
curl -fL --retry 3 "$SRC" -o src.tar.xz
printf '%s  %s\n' "$SHA" src.tar.xz | sha256sum -c -
tar -xf src.tar.xz
cd "libxml2-$VER"
echo "Building compatibility library under $PREFIX (no system library changes)..."
./configure --prefix="$PREFIX" --without-python --without-lzma --without-zlib --without-readline --without-icu --disable-static
make -j2
make install
if [ ! -e "$PREFIX/lib/libxml2.so.2" ]; then
 echo "Expected libxml2.so.2 not found after build." >&2
 exit 1
fi
mkdir -p "$HOME/.local/bin"
if [ -e "$HOME/.local/bin/clangd" ]; then
 cp "$HOME/.local/bin/clangd" "$HOME/.local/bin/clangd.bak.$(date +%Y%m%d%H%M%S)"
fi
cat > "$HOME/.local/bin/clangd" <<EOF
#!/bin/sh
LD_LIBRARY_PATH="$PREFIX/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" exec /usr/bin/clangd "\$@"
EOF
chmod 755 "$HOME/.local/bin/clangd"
echo "Private clangd wrapper created: $HOME/.local/bin/clangd"
echo "Checking clangd..."
"$HOME/.local/bin/clangd" --version
echo "Checking shared libraries..."
LD_LIBRARY_PATH="$PREFIX/lib" ldd /usr/bin/clangd | grep 'not found' && {
 echo "Other missing libraries remain." >&2; exit 1;
} || true
echo "If ~/.local/bin is in your PATH, reopen Neovim and :edit your C file."
echo "Otherwise run: fish_add_path ~/.local/bin"
