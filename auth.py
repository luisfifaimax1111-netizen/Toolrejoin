import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(os.environ.get("REJOIN_HOME", str(Path.home() / ".rejoin")))
LIC = APP_DIR / "license.json"
URL = ("https://raw.githubusercontent.com/luisfifaimax1111-netizen/"
       "Toolrejoin/refs/heads/main/keys.json")
SALT = "tuat-tech-2026"


def hash_key(k):
    return hashlib.sha256((SALT + k.strip().upper()).encode()).hexdigest()


def fetch():
    try:
        req = urllib.request.Request(URL + "?t=%d" % time.time())
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("keys", [])
    except Exception:
        return None


def valid(keys, h):
    for e in keys:
        if e.get("h") == h:
            exp = float(e.get("exp", 0) or 0)
            return exp == 0 or exp > time.time()
    return False


def require():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        saved = json.loads(LIC.read_text()).get("h")
    except Exception:
        saved = None
    keys = fetch()
    if saved and keys is not None and valid(keys, saved):
        return True
    if saved and keys is None:
        print("Khong co mang, dung key da luu.")
        return True
    for n in range(3):
        k = input("Nhap key: ").strip()
        keys = fetch()
        if keys is None:
            print("Khong tai duoc danh sach key, kiem tra mang.")
            return False
        h = hash_key(k)
        if valid(keys, h):
            LIC.write_text(json.dumps({"h": h}))
            print("Key hop le!")
            return True
        print("Key sai hoac het han (%d/3)" % (n + 1))
    return False
