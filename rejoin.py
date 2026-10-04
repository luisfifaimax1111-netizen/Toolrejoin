#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rejoin.py - Roblox Auto Rejoin cho Termux (đa cửa sổ / nhiều bản clone)

Lệnh:
  scan                         Quét các bản Roblox đã cài
  add --auto --place ID        Thêm tất cả bản Roblox tìm thấy với cùng Place ID
  add --pkg PKG --place ID     Thêm 1 bản cụ thể (--name, --link cho private server)
  list / remove NAME / set KEY VALUE
  join PLACE [-p PKG]          Vào game ngay 1 lần
  start [--place ID] [--fresh] Bắt đầu treo + bảng trạng thái
  status [--watch]             Xem trạng thái từ session khác
  stop                         Dừng tool đang chạy
"""
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(os.environ.get("REJOIN_HOME", str(Path.home() / ".rejoin")))
CFG_FILE = APP_DIR / "config.json"
STATUS_FILE = APP_DIR / "status.json"
PID_FILE = APP_DIR / "rejoin.pid"
LOG_FILE = APP_DIR / "rejoin.log"

DEFAULT_SETTINGS = {
    "check_interval": 8,        # giây giữa mỗi lần kiểm tra
    "launch_grace": 60,         # thời gian chờ game load sau khi mở
    "rejoin_delay": 4,          # chờ trước khi vào lại
    "stagger": 15,              # giãn cách giữa các cửa sổ lúc khởi động
    "force_rejoin_minutes": 0,  # rejoin định kỳ (0 = tắt)
    "max_rejoins": 0,           # giới hạn số lần vào lại (0 = vô hạn)
    "backoff_max": 120,         # delay tối đa khi văng liên tục
    "use_root": "auto",         # auto | yes | no
    "log_check": True,          # đọc log Roblox để bắt disconnect (cần root)
    "webhook": "",              # Discord webhook URL (tuỳ chọn)
}

LOG_DIRS = [
    "/data/data/{pkg}/files/logs",
    "/data/data/{pkg}/cache/logs",
    "/sdcard/Android/data/{pkg}/files/logs",
]
DISCONNECT_RE = re.compile(
    r"(disconnected from game|connection (was )?lost|you were kicked|"
    r"error code:? ?(277|279|268|267|264|273|288|529))",
    re.I,
)

C = {
    "r": "\033[31m", "g": "\033[32m", "y": "\033[33m", "b": "\033[34m",
    "m": "\033[35m", "c": "\033[36m", "w": "\033[97m", "d": "\033[90m",
    "x": "\033[0m", "B": "\033[1m",
}


def col(txt, *keys):
    return "".join(C[k] for k in keys) + str(txt) + C["x"]


# ───────────────────────── Shell helper ─────────────────────────
class Shell:
    root = False

    @staticmethod
    def _raw(argv, timeout):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return (p.stdout or "") + (p.stderr or "")
        except Exception:
            return None

    @classmethod
    def detect(cls, mode):
        if mode == "no":
            cls.root = False
            return
        out = cls._raw(["su", "-c", "id -u"], 6)
        cls.root = bool(out) and out.strip().splitlines()[-1].strip() == "0"
        if mode == "yes" and not cls.root:
            print(col("Không lấy được root dù set use_root=yes, đm.", "r"))

    @classmethod
    def sh(cls, cmd, timeout=15, force_user=False):
        if cls.root and not force_user:
            return cls._raw(["su", "-c", cmd], timeout)
        return cls._raw(["sh", "-c", cmd], timeout)


# ───────────────────────── Config ─────────────────────────
def load_cfg():
    data = {}
    if CFG_FILE.exists():
        try:
            data = json.loads(CFG_FILE.read_text("utf-8"))
        except Exception:
            data = {}
    data.setdefault("settings", {})
    for k, v in DEFAULT_SETTINGS.items():
        data["settings"].setdefault(k, v)
    data.setdefault("instances", [])
    return data


def save_cfg(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CFG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


def log_event(msg):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def fmt_dur(sec):
    sec = int(max(0, sec))
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


# ───────────────────────── Android helpers ─────────────────────────
def scan_packages():
    out = Shell.sh("pm list packages 2>/dev/null || cmd package list packages 2>/dev/null")
    pkgs = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("package:") and "roblox" in line.lower():
            pkgs.append(line.split(":", 1)[1])
    return sorted(set(pkgs
