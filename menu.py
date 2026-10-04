#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu.py - Menu số cho Roblox Auto Rejoin (đặt cùng thư mục với rejoin.py)
Chạy: python menu.py
"""
import argparse
import json
import os
import shlex
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request

try:
    import rejoin as R
except ImportError:
    sys.exit("Không thấy rejoin.py cùng thư mục, tổng tài duck.")

col = R.col
VERSION = "1.0"


# ───────────────────────── UI ─────────────────────────
def clear():
    sys.stdout.write("\033[H\033[J")
    sys.stdout.flush()


def banner():
    clear()
    w = min(shutil.get_terminal_size((44, 20)).columns, 52)
    print(col("═" * w, "c"))
    print(col("   TECH  ·  ROBLOX AUTO REJOIN", "B", "c"))
    print(col("  Version %s · Termux" % VERSION, "d"))
    print(col("═" * w, "c"))


def ask(prompt, default=""):
    try:
        suffix = " [%s]" % default if default else ""
        v = input(col("[Tuất] ", "c") + prompt + suffix + ": ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return v or default


def pause():
    try:
        input(col("\nEnter để quay lại menu...", "d"))
    except (EOFError, KeyboardInterrupt):
        pass


def ok(msg):
    print(col("✔ ", "g") + msg)


def bad(msg):
    print(col("✘ ", "r") + msg)


def warn(msg):
    print(col("! ", "y") + msg)


# ───────────────────────── Helpers ─────────────────────────
def init():
    cfg = R.load_cfg()
    cfg["settings"].setdefault("package_prefix", "com.roblox")
    R.Shell.detect(cfg["settings"]["use_root"])
    return cfg


def get_pkgs(cfg):
    prefix = cfg["settings"].get("package_prefix", "com.roblox")
    out = R.Shell.sh("pm list packages 2>/dev/null || cmd package list packages 2>/dev/null")
    pkgs = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            name = line.split(":", 1)[1]
            if name.startswith(prefix):
                pkgs.append(name)
    return sorted(set(pkgs))


def has_place(i):
    return str(i.get("place_id", "")).isdigit()


def ensure_inst(cfg, pkg, place=None):
    for i in cfg["instances"]:
        if i["package"] == pkg:
            if place:
                i["place_id"] = str(place)
            return i
    inst = {"name": "acc%d" % (len(cfg["instances"]) + 1), "package": pkg,
            "place_id": str(place or ""), "link_code": "", "enabled": True}
    cfg["instances"].append(inst)
    return inst


# ───────────────────────── 1. Start ─────────────────────────
def m_start():
    cfg = init()
    ready = [i for i in cfg["instances"] if i.get("enabled", True) and has_place(i)]
    if not ready:
        bad("Chưa có package nào có Game ID. Vào mục 2 setup trước.")
        return pause()
    print("Sẽ treo %d cửa sổ:" % len(ready))
    for i in ready:
        print("  •", i["name"], i["package"], "→", i["place_id"])
    fresh = ask("Tắt game cũ trước khi vào? (y/n)", "y").lower() == "y"
    if ask("Bắt đầu? (y/n)", "y").lower() != "y":
        return
    R.cmd_start(argparse.Namespace(place=None, fresh=fresh, names=[]))
    pause()


# ───────────────────────── 2. Setup Game ID ─────────────────────────
def m_setup():
    cfg = init()
    pkgs = get_pkgs(cfg)
    if not pkgs:
        bad("Không thấy package nào với prefix '%s'. Vào mục 6 chỉnh prefix."
            % cfg["settings"]["package_prefix"])
        return pause()
    print("Tìm thấy %d package:" % len(pkgs))
    for n, p in enumerate(pkgs, 1):
        cur = next((i["place_id"] for i in cfg["instances"] if i["package"] == p), "")
        tag = col("(ID: %s)" % cur, "d") if cur else ""
        print("  [%d] %s %s" % (n, p, tag))
    print("\n  [1] Dùng 1 Game ID cho TẤT CẢ package")
    print("  [2] Đặt Game ID riêng từng package")
    mode = ask("Chọn", "1")
    if mode == "1":
        pid = ask("Nhập Place ID")
        if not pid.isdigit():
            bad("Place ID phải là số.")
            return pause()
        link = ask("Link code private server (Enter để bỏ qua)")
        for p in pkgs:
            inst = ensure_inst(cfg, p, pid)
            inst["link_code"] = link
    else:
        for p in pkgs:
            pid = ask("Place ID cho %s (Enter bỏ qua)" % p)
            if pid.isdigit():
                inst = ensure_inst(cfg, p, pid)
                inst["link_code"] = ask("  Link code private (Enter bỏ qua)")
    R.save_cfg(cfg)
    ok("Đã lưu cấu hình.")
    pause()


# ───────────────────────── 3. Cookie login ─────────────────────────
def cookie_db_candidates(pkg):
    base = "/data/data/%s/app_webview/Default" % pkg
    return [base + "/Cookies", base + "/Network/Cookies"]


def inject_cookie(pkg, cookie):
    """Ghi .ROBLOSECURITY vào WebView của bản Roblox (cần root, acc của chính mình)."""
    if not R.Shell.root:
        return False, "cần root"
    if len(cookie) < 100:
        return False, "cookie có vẻ sai/thiếu"
    q = shlex.quote
    R.force_stop(pkg)
    db = None
    for c in cookie_db_candidates(pkg):
        out = R.Shell.sh("test -f %s && echo yes" % q(c))
        if out and "yes" in out:
            db = c
            break
    if not db:
        return False, "chưa thấy DB cookie, mở app Roblox 1 lần rồi tắt đi"

    tmpdir = tempfile.mkdtemp()
    local = os.path.join(tmpdir, "Cookies")
    R.Shell.sh("cp %s %s && chmod 666 %s" % (q(db), q(local), q(local)))
    try:
        con = sqlite3.connect(local)
        info = con.execute("PRAGMA table_info(cookies)").fetchall()
        if not info:
            return False, "DB cookie không đúng định dạng"
        now = int((time.time() + 11644473600) * 1000000)
        exp = now + 365 * 86400 * 1000000
        known = {
            "creation_utc": now, "host_key": ".roblox.com", "top_frame_site_key": "",
            "name": ".ROBLOSECURITY", "value": cookie, "encrypted_value": b"",
            "path": "/", "expires_utc": exp, "is_secure": 1, "is_httponly": 1,
            "last_access_utc": now, "has_expires": 1, "is_persistent": 1,
            "priority": 1, "samesite": -1, "source_scheme": 2, "source_port": 443,
            "is_same_party": 0, "last_update_utc": now, "source_type": 0,
            "has_cross_site_ancestor": 0,
        }
        row = {}
        for _, name, ctype, *_ in info:
            ct = (ctype or "").upper()
            if name in known:
                row[name] = known[name]
            elif "BLOB" in ct:
                row[name] = b""
            elif "CHAR" in ct or "TEXT" in ct:
                row[name] = ""
            else:
                row[name] = 0
        con.execute("DELETE FROM cookies WHERE name='.ROBLOSECURITY'")
        keys = list(row)
        sql = "INSERT INTO cookies (%s) VALUES (%s)" % (
            ",".join(keys), ",".join("?" * len(keys)))
        con.execute(sql, [row[k] for k in keys])
        con.commit()
        con.close()
    except Exception as e:
        return False, "lỗi sqlite: %r" % e

    uid_out = R.Shell.sh("stat -c %%u %s" % q("/data/data/" + pkg)) or ""
    lines = uid_out.strip().splitlines()
    uid = lines[-1] if lines else ""
    R.Shell.sh("rm -f %s-wal %s-journal %s-shm" % (q(db), q(db), q(db)))
    R.Shell.sh("cp %s %s" % (q(local), q(db)))
    if uid.isdigit():
        R.Shell.sh("chown %s:%s %s && chmod 660 %s" % (uid, uid, q(db), q(db)))
    R.Shell.sh("restorecon %s 2>/dev/null" % q(db))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return True, "ok"


def m_cookie():
    cfg = init()
    if not R.Shell.root:
        bad("Mục này cần root.")
        return pause()
    pkgs = get_pkgs(cfg)
    if not pkgs:
        bad("Không thấy package nào.")
        return pause()
    warn("Cookie = chìa khoá acc. Chỉ dùng acc của chính mình, đừng gửi cho ai.")
    print("  [1] Nhập cookie mới cho từng package")
    print("  [2] Dùng lại cookie đã lưu")
    mode = ask("Chọn", "1")
    for p in pkgs:
        inst = ensure_inst(cfg, p)
        if mode == "1":
            ck = ask("Cookie cho %s (Enter bỏ qua)" % p)
            if ck:
                inst["cookie"] = ck
        ck = inst.get("cookie", "")
        if not ck:
            warn("%s: không có cookie, bỏ qua" % p)
            continue
        good, info = inject_cookie(p, ck)
        show = ok if good else bad
        show("%s: %s" % (p, info))
    R.save_cfg(cfg)
    try:
        os.chmod(R.CFG_FILE, 0o600)
    except Exception:
        pass
    pause()


# ───────────────────────── 4. Webhook ─────────────────────────
def m_webhook():
    cfg = init()
    cur = cfg["settings"].get("webhook", "")
    print("Webhook hiện tại:", cur or col("(tắt)", "d"))
    print("  [1] Đặt/đổi URL    [2] Tắt    [3] Gửi tin thử")
    c = ask("Chọn", "1")
    if c == "1":
        url = ask("Dán Discord webhook URL")
        if url.startswith("https://"):
            cfg["settings"]["webhook"] = url
            R.save_cfg(cfg)
            ok("Đã lưu.")
        else:
            bad("URL không hợp lệ.")
    elif c == "2":
        cfg["settings"]["webhook"] = ""
        R.save_cfg(cfg)
        ok("Đã tắt webhook.")
    elif c == "3" and cur:
        try:
            body = json.dumps({"content": "Tuất Tech: webhook ok"}).encode()
            req = urllib.request.Request(
                cur, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
            ok("Đã gửi, check Discord.")
        except Exception as e:
            bad("Gửi lỗi: %r" % e)
    pause()


# ───────────────────────── 5. Check setup ─────────────────────────
def m_check():
    cfg = init()
    s = cfg["settings"]
    if R.Shell.root:
        ok("Root: CÓ")
    else:
        warn("Root: KHÔNG (chạy chế độ treo mù)")
    if shutil.which("termux-wake-lock"):
        ok("termux-wake-lock: có")
    else:
        warn("termux-wake-lock: thiếu -> pkg install termux-api")
    pkgs = get_pkgs(cfg)
    show = ok if pkgs else bad
    show("Package (prefix '%s'): %d" % (s["package_prefix"], len(pkgs)))
    ready = [i for i in cfg["instances"] if has_place(i)]
    show = ok if ready else bad
    show("Package đã có Game ID: %d/%d" % (len(ready), len(pkgs)))
    for p in pkgs:
        done = any(i["package"] == p and has_place(i) for i in cfg["instances"])
        if not done:
            warn("  chưa setup: " + p)
    hasck = sum(1 for i in cfg["instances"] if i.get("cookie"))
    print("  Cookie đã lưu: %d" % hasck)
    if s.get("webhook"):
        ok("Webhook: bật")
    else:
        warn("Webhook: tắt")
    if R.Shell.root and pkgs:
        for p in pkgs:
            state = col("đang chạy", "g") if R.is_running(p) else col("đang tắt", "d")
            print("  %s: %s" % (p, state))
    pause()


# ───────────────────────── 6. Prefix ─────────────────────────
def m_prefix():
    cfg = init()
    cur = cfg["settings"]["package_prefix"]
    print("Prefix hiện tại:", col(cur, "y"))
    print(col("Ví dụ: com.roblox (bản gốc + clone com.roblox.xxx)", "d"))
    new = ask("Nhập prefix mới (Enter giữ nguyên)", cur)
    cfg["settings"]["package_prefix"] = new
    R.save_cfg(cfg)
    found = get_pkgs(cfg)
    ok("Đã lưu. Tìm thấy %d package:" % len(found))
    for p in found:
        print("  •", p)
    pause()


# ───────────────────────── Main loop ─────────────────────────
MENU = [
    ("1", "Start Auto Rejoin", m_start),
    ("2", "Setup Game ID cho Packages", m_setup),
    ("3", "Auto Login bằng Cookie (root)", m_cookie),
    ("4", "Discord Webhook", m_webhook),
    ("5", "Auto Check Setup", m_check),
    ("6", "Cấu hình Package Prefix", m_prefix),
    ("0", "Thoát", None),
]


def main():
    while True:
        banner()
        for k, label, _ in MENU:
            print("  %s  %s" % (col("[%s]" % k, "B", "y"), label))
        print(col("─" * 44, "d"))
        c = ask("Nhập lệnh")
        if c == "0":
            print("Tạm biệt tổng tài technologia.")
            return
        fn = next((f for k, _, f in MENU if k == c), None)
        if fn is None:
            continue
        banner()
        try:
            fn()
        except KeyboardInterrupt:
            print()
        except Exception as e:
            bad("Lỗi: %r" % e)
            pause()


if __name__ == "__main__":
    main()
