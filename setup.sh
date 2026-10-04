#!/data/data/com.termux/files/usr/bin/bash
# setup.sh - Cài đặt Tuất Tech Rejoin Tool (Termux)

GH_USER="luisfifaimax1111-netizen"
GH_REPO="Toolrejoin"
BRANCH="main"
BASE="https://raw.githubusercontent.com/${GH_USER}/${GH_REPO}/refs/heads/${BRANCH}"
INSTALL_DIR="$HOME/toolrejoin"
BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/tuat"

R="\033[31m"; G="\033[32m"; Y="\033[33m"; C="\033[36m"; X="\033[0m"
say()  { echo -e "${C}[Tuất]${X} $*"; }
ok()   { echo -e "${G}✔${X} $*"; }
fail() { echo -e "${R}✘${X} $*"; exit 1; }

clear
echo -e "${C}══════════════════════════════════════${X}"
echo -e "${C}  TECH BỐ RYN · ROBLOX AUTO REJOIN${X}"
echo -e "${C}══════════════════════════════════════${X}"

# 1. Kiểm tra Termux
[ -d "/data/data/com.termux" ] || fail "Chỉ chạy được trong Termux, tổng tài duck."

# 2. Cài gói cần thiết
say "Cập nhật gói..."
yes | pkg update -y >/dev/null 2>&1
for p in python curl nano termux-api; do
    if ! command -v "$p" >/dev/null 2>&1 && ! pkg list-installed 2>/dev/null | grep -q "^$p/"; then
        say "Cài $p..."
        pkg install -y "$p" >/dev/null 2>&1 || echo -e "${Y}!${X} Không cài được $p (bỏ qua)"
    fi
done
ok "Gói đã sẵn sàng"

# 3. Cấp quyền bộ nhớ (nếu chưa có)
[ -d "$HOME/storage" ] || termux-setup-storage

# 4. Tải file tool
mkdir -p "$INSTALL_DIR"
for f in rejoin.py menu.py; do
    say "Tải $f..."
    if curl -fsSL "$BASE/$f" -o "$INSTALL_DIR/$f.tmp"; then
        mv "$INSTALL_DIR/$f.tmp" "$INSTALL_DIR/$f"
        ok "$f"
    else
        rm -f "$INSTALL_DIR/$f.tmp"
        fail "Tải $f lỗi. Kiểm tra repo có Public không và link đúng chưa."
    fi
done

# 5. Tạo lệnh `tuat`
cat > "$BIN" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
export TERM=xterm-256color
cd "$INSTALL_DIR" && exec python menu.py "\$@"
EOF
chmod +x "$BIN"
ok "Đã tạo lệnh: tuat"

echo
echo -e "${G}Cài xong!${X} Gõ ${Y}tuat${X} để mở menu."
read -r -p "Mở menu luôn? (y/n) [y]: " a
[ "${a:-y}" = "y" ] && exec tuat
