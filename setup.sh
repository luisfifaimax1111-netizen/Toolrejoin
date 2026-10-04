#!/data/data/com.termux/files/usr/bin/bash
# setup.sh - Cai dat Tuat Tech Rejoin Tool (Termux)

GH_USER="luisfifaimax1111-netizen"
GH_REPO="Toolrejoin"
BRANCH="main"
BASE="https://raw.githubusercontent.com/${GH_USER}/${GH_REPO}/refs/heads/${BRANCH}"
INSTALL_DIR="$HOME/toolrejoin"
BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/tuat"

R="\033[31m"; G="\033[32m"; Y="\033[33m"; C="\033[36m"; X="\033[0m"
say()  { echo -e "${C}[Tuat]${X} $*"; }
ok()   { echo -e "${G}OK${X} $*"; }
fail() { echo -e "${R}LOI${X} $*"; exit 1; }

clear
echo -e "${C}==============================${X}"
echo -e "${C}  TECH BỐ CỦA RYN - ROBLOX REJOIN${X}"
echo -e "${C}==============================${X}"

[ -d "/data/data/com.termux" ] || fail "Chi chay duoc trong Termux."

say "Cap nhat goi..."
yes | pkg update -y >/dev/null 2>&1
for p in python curl nano termux-api; do
    if ! command -v "$p" >/dev/null 2>&1 && ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
        say "Cai $p..."
        pkg install -y "$p" >/dev/null 2>&1 || echo -e "${Y}!${X} Khong cai duoc $p (bo qua)"
    fi
done
ok "Goi da san sang"

[ -d "$HOME/storage" ] || termux-setup-storage

mkdir -p "$INSTALL_DIR"
for f in rejoin.py menu.py auth.py; do
    say "Tai $f..."
    if curl -fsSL "$BASE/$f" -o "$INSTALL_DIR/$f.tmp"; then
        mv "$INSTALL_DIR/$f.tmp" "$INSTALL_DIR/$f"
        ok "$f"
    else
        rm -f "$INSTALL_DIR/$f.tmp"
        fail "Tai $f loi. Kiem tra repo Public va link dung chua."
    fi
done

cat > "$BIN" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
export TERM=xterm-256color
cd "$INSTALL_DIR" && exec python menu.py "\$@"
EOF
chmod +x "$BIN"
ok "Da tao lenh: tuat"

echo
echo -e "${G}Cai xong!${X} Go ${Y}tuat${X} de mo menu."
read -r -p "Mo menu luon? (y/n) [y]: " a
[ "${a:-y}" = "y" ] && exec tuat
