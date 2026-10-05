import hashlib
import json
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

WORKER = "https://tuatkey.luisfifaimax1111.workers.dev"
OFFLINE_GRACE = 24 * 3600

APP_DIR = Path(os.environ.get("REJOIN_HOME", str(Path.home() / ".rejoin")))
LIC = APP_DIR / "license.json"
DEV_FILE = APP_DIR / "device_id"
_STATE = {"ok": None, "exp": 0, "ts": 0}


def device_id():
    try:
        return DEV_FILE.read_text().strip()
    except Exception:
        pass
    raw = ""
    try:
        r = subprocess.run(["settings", "get", "secure", "android_id"],
                           capture_output=True, text=True, timeout=5)
        raw = r.stdout.strip()
    except Exception:
        pass
    if not raw or raw == "null" or len(raw) < 8 or " " in raw:
        raw = uuid.uuid4().hex
    dev = hashlib.sha256(("dev" + raw).encode()).hexdigest()[:24]
    APP_DIR.mkdir(parents=True, exist_ok=True)
    DEV_FILE.write_text(dev)
    return dev


def check(key):
    body = json.dumps({"key": key, "dev": device_id()}).encode()
    req = urllib.request.Request(
        WORKER + "/check", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "TuatRejoin/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            js = json.loads(r.read().decode())
        _STATE["ok"] = bool(js.get("ok"))
        _STATE["ts"] = time.time()
        if js.get("ok"):
            _STATE["exp"] = js.get("exp") or 0
        return js.get("ok", False), js.get("msg", "")
    except Exception as e:
        return None, "%r" % e


def load_lic():
    try:
        return json.loads(LIC.read_text())
    except Exception:
        return {}


def require():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    lic = load_lic()
    saved = lic.get("key")
    if saved:
        ok, msg = check(saved)
        if ok:
            lic["verified"] = time.time()
            LIC.write_text(json.dumps(lic))
            return True
        if ok is None:
            if time.time() - lic.get("verified", 0) < OFFLINE_GRACE:
                print("Khong co mang, dung key da luu.")
                return True
            print("Can mang de xac thuc key.")
            return False
        print("Key khong con hop le: " + msg)
        try:
            LIC.unlink()
        except Exception:
            pass
    for n in range(3):
        k = input("Nhap key: ").strip()
        ok, msg = check(k)
        if ok:
            LIC.write_text(json.dumps({"key": k.upper(), "verified": time.time()}))
            print(msg)
            return True
        if ok is None:
            print("Khong ket noi duoc may chu, kiem tra mang.")
            return False
        print("%s (%d/3)" % (msg, n + 1))
    return False


def key_status():
    """Tra ve (chu, mau) de hien tren dau menu."""
    key = load_lic().get("key")
    if not key:
        return "KHONG HOAT DONG", "r"
    if time.time() - _STATE["ts"] > 300:
        check(key)
        _STATE["ts"] = time.time()
    if _STATE["ok"] is None:
        return "OFFLINE (dung key da luu)", "y"
    if _STATE["ok"] is False:
        return "KHONG HOAT DONG", "r"
    exp = _STATE["exp"]
    if not exp:
        return "HOAT DONG - vinh vien", "g"
    left = int(exp - time.time())
    if left <= 0:
        return "HET HAN", "r"
    d, rem = divmod(left, 86400)
    h, rem = divmod(rem, 3600)
    return "HOAT DONG - con %dn %dg %dp" % (d, h, rem // 60), "g"
