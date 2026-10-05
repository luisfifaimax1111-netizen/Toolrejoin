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
    "check_interval": 8,
    "launch_grace": 60,
    "rejoin_delay": 4,
    "stagger": 15,
    "force_rejoin_minutes": 0,
    "max_rejoins": 0,
    "backoff_max": 120,
    "use_root": "auto",
    "log_check": True,
    "webhook": "",
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
    return sorted(set(pkgs))


def is_running(pkg):
    """True / False / None (không xác định được - không root)."""
    if not Shell.root:
        return None
    out = Shell.sh("pidof %s" % shlex.quote(pkg))
    if out and re.search(r"\d", out) and "not found" not in out.lower():
        return True
    out = Shell.sh("ps -A 2>/dev/null | grep -F %s" % shlex.quote(pkg))
    for line in (out or "").splitlines():
        parts = line.split()
        if parts and parts[-1] == pkg:
            return True
    return False


def force_stop(pkg):
    if Shell.root:
        Shell.sh("am force-stop %s" % shlex.quote(pkg))
        time.sleep(1.5)


def build_uri(place, link=""):
    uri = "roblox://placeId=%s" % place
    if link:
        uri += "&linkCode=%s" % link
    return uri


def launch_game(pkg, place, link=""):
    cmd = "am start -a android.intent.action.VIEW -d %s -p %s" % (
        shlex.quote(build_uri(place, link)), shlex.quote(pkg))
    out = Shell.sh(cmd, timeout=20)
    if out is None:
        return False, "không chạy được lệnh am"
    low = out.lower()
    if "error" in low or "not started" in low or "unable to resolve" in low:
        return False, out.strip().splitlines()[-1][:80]
    return True, "ok"


def send_webhook(url, msg):
    if not url:
        return

    def _go():
        try:
            data = json.dumps({"content": msg}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


# ───────────────────────── Log watcher ─────────────────────────
class LogWatcher:
    def __init__(self, pkg):
        self.pkg = pkg
        self.file = None
        self.offset = 0

    def _newest(self):
        for d in LOG_DIRS:
            d = d.format(pkg=self.pkg)
            out = Shell.sh("ls -t %s 2>/dev/null | head -n 1" % shlex.quote(d))
            if out and out.strip() and "no such" not in out.lower():
                return d + "/" + out.strip().splitlines()[0]
        return None

    @staticmethod
    def _size(f):
        out = Shell.sh("stat -c %%s %s 2>/dev/null" % shlex.quote(f))
        try:
            return int(out.strip())
        except Exception:
            return 0

    def reset(self):
        self.file = self._newest()
        self.offset = self._size(self.file) if self.file else 0

    def poll(self):
        f = self._newest()
        if not f:
            return None
        if f != self.file:
            self.file, self.offset = f, 0
        size = self._size(f)
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return None
        out = Shell.sh("tail -c +%d %s" % (self.offset + 1, shlex.quote(f)))
        self.offset = size
        if out:
            m = DISCONNECT_RE.search(out)
            if m:
                return m.group(0)
        return None


# ───────────────────────── Instance worker ─────────────────────────
class Instance(threading.Thread):
    def __init__(self, conf, settings, stop_evt, delay=0, fresh=False):
        super().__init__(daemon=True)
        self.conf = conf
        self.s = settings
        self.stop_evt = stop_evt
        self.delay = delay
        self.fresh = fresh
        self.name_ = conf["name"]
        self.pkg = conf["package"]
        self.place = conf["place_id"]
        self.link = conf.get("link_code", "")
        self.watcher = LogWatcher(self.pkg)
        self.lock = threading.Lock()
        self.status = "WAITING"
        self.disconnects = 0
        self.rejoins = 0
        self.session_start = 0.0
        self.last_launch = 0.0
        self.seen_alive = False
        self.fail_streak = 0
        self.last_event = "Chờ khởi động"
        self.last_event_ts = time.time()

    def event(self, msg, notify=False):
        with self.lock:
            self.last_event = msg
            self.last_event_ts = time.time()
        log_event("[%s] %s" % (self.name_, msg))
        if notify:
            send_webhook(self.s.get("webhook", ""),
                         "**%s** (%s): %s" % (self.name_, self.place, msg))

    def snapshot(self):
        with self.lock:
            up = 0
            if self.status in ("RUNNING", "LAUNCHING", "BLIND") and self.session_start:
                up = time.time() - self.session_start
            return {
                "name": self.name_, "package": self.pkg, "place_id": self.place,
                "status": self.status, "uptime": up,
                "disconnects": self.disconnects, "rejoins": self.rejoins,
                "last_event": self.last_event, "last_event_ts": self.last_event_ts,
            }

    def set_status(self, st):
        with self.lock:
            self.status = st

    def do_launch(self, count_rejoin):
        alive = is_running(self.pkg)
        if alive or self.fresh:
            force_stop(self.pkg)
        self.fresh = False
        self.set_status("LAUNCHING")
        ok, info = False, ""
        for _ in range(3):
            ok, info = launch_game(self.pkg, self.place, self.link)
            if ok:
                break
            if self.stop_evt.wait(3):
                return False
        now = time.time()
        self.last_launch = now
        self.seen_alive = False
        if not ok:
            self.set_status("ERROR")
            self.event("Mở game lỗi: %s" % info)
            return False
        self.session_start = now
        if Shell.root and self.s.get("log_check"):
            self.watcher.reset()
        if count_rejoin:
            with self.lock:
                self.rejoins += 1
            self.event("Đã vào lại game (lần %d)" % self.rejoins, notify=True)
        else:
            self.event("Đã gửi lệnh vào game %s" % self.place)
        if not Shell.root:
            self.set_status("BLIND")
        return True

    def handle_disconnect(self, reason, kill):
        with self.lock:
            self.disconnects += 1
            n = self.disconnects
            self.status = "DISCONNECTED"
        self.event("Văng lần %d: %s" % (n, reason), notify=True)

        mx = int(self.s.get("max_rejoins", 0))
        if mx and self.rejoins >= mx:
            self.set_status("LIMIT")
            self.event("Đã đạt giới hạn %d lần vào lại" % mx)
            return False

        quick = (time.time() - self.last_launch) < self.s["launch_grace"] * 2
        self.fail_streak = self.fail_streak + 1 if quick else 0
        delay = min(self.s["rejoin_delay"] * (2 ** min(self.fail_streak, 5)),
                    self.s["backoff_max"])
        if delay > self.s["rejoin_delay"]:
            self.event("Văng liên tục, chờ %ds rồi vào lại" % delay)
        if self.stop_evt.wait(delay):
            return False
        if kill:
            force_stop(self.pkg)
        self.do_launch(count_rejoin=True)
        return True

    def tick(self):
        now = time.time()
        st = self.status
        if st == "LIMIT":
            return
        in_grace = (now - self.last_launch) < self.s["launch_grace"]
        alive = is_running(self.pkg)

        if alive is None:
            self.set_status("BLIND")
        elif alive:
            if not self.seen_alive:
                self.seen_alive = True
                self.set_status("RUNNING")
                self.event("Game đang chạy")
            elif st in ("LAUNCHING", "ERROR", "DISCONNECTED"):
                self.set_status("RUNNING")
            if self.s.get("log_check") and (now - self.last_launch) > 15:
                hit = self.watcher.poll()
                if hit:
                    self.handle_disconnect("Log báo '%s'" % hit, kill=True)
                    return
            if (now - self.last_launch) > self.s["launch_grace"] * 2:
                self.fail_streak = 0
        else:
            if self.seen_alive:
                self.handle_disconnect("Game bị tắt/văng", kill=False)
                return
            if not in_grace:
                self.event("Chưa mở được game, thử lại")
                self.do_launch(count_rejoin=False)
                return

        fm = float(self.s.get("force_rejoin_minutes", 0))
        if fm > 0 and (now - self.last_launch) > fm * 60:
            self.event("Rejoin định kỳ (%g phút)" % fm)
            mx = int(self.s.get("max_rejoins", 0))
            if mx and self.rejoins >= mx:
                self.set_status("LIMIT")
                return
            force_stop(self.pkg)
            self.do_launch(count_rejoin=True)

    def run(self):
        if self.delay and self.stop_evt.wait(self.delay):
            return
        self.do_launch(count_rejoin=False)
        while not self.stop_evt.wait(self.s["check_interval"]):
            try:
                self.tick()
            except Exception as e:
                self.event("Lỗi nội bộ: %r" % e)
        self.set_status("STOPPED")


# ───────────────────────── Render ─────────────────────────
STATUS_VIEW = {
    "WAITING": ("CHỜ", "c"), "LAUNCHING": ("ĐANG VÀO GAME", "y"),
    "RUNNING": ("ĐANG CHẠY", "g"), "DISCONNECTED": ("VĂNG GAME", "r"),
    "ERROR": ("LỖI MỞ GAME", "r"), "LIMIT": ("ĐẠT GIỚI HẠN", "m"),
    "STOPPED": ("ĐÃ DỪNG", "d"), "BLIND": ("TREO (không root)", "g"),
}


_KEY_CACHE = {"txt": "Key: dang kiem tra...", "ts": 0, "busy": False}


def key_line():
    now = time.time()
    if now - _KEY_CACHE["ts"] > 300 and not _KEY_CACHE["busy"]:
        _KEY_CACHE["busy"] = True

        def _work():
            try:
                import auth
                t, c = auth.key_status()
                _KEY_CACHE["txt"] = "Key: " + col(t, c)
            except Exception:
                _KEY_CACHE["txt"] = "Key: " + col("khong ro", "y")
            _KEY_CACHE["ts"] = time.time()
            _KEY_CACHE["busy"] = False
        threading.Thread(target=_work, daemon=True).start()
    return _KEY_CACHE["txt"]


def render(snaps, root, started):
    width = min(shutil.get_terminal_size((48, 20)).columns, 60)
    line = col("─" * width, "d")
    out = [col(" ROBLOX AUTO REJOIN ", "B", "c") + col("| Tuất Tech", "d"), line]
    td = sum(s["disconnects"] for s in snaps)
    tr = sum(s["rejoins"] for s in snaps)
    run = sum(1 for s in snaps if s["status"] in ("RUNNING", "BLIND"))
    out.append("Root: %s | Chạy: %s | Tổng văng: %s | Vào lại: %s" % (
        col("CÓ", "g") if root else col("KHÔNG", "y"),
        col("%d/%d" % (run, len(snaps)), "g"), col(td, "r"), col(tr, "y")))
    out.append("Tool chạy được: " + col(fmt_dur(time.time() - started), "w"))
    out.append(line)
    for i, s in enumerate(snaps, 1):
        label, color = STATUS_VIEW.get(s["status"], (s["status"], "w"))
        out.append("%s %s  %s" % (col("[%d]" % i, "d"), col(s["name"], "B", "w"),
                                  col("● " + label, color)))
        out.append("    ID: %s | %s" % (s["place_id"], s["package"].split(".")[-1]))
        out.append("    ⏱ %s | Văng: %s | Vào lại: %s" % (
            fmt_dur(s["uptime"]), col(s["disconnects"], "r"), col(s["rejoins"], "y")))
        out.append("    ↳ %s %s" % (
            col(time.strftime("%H:%M:%S", time.localtime(s["last_event_ts"])), "d"),
            s["last_event"][: width - 18]))
        out.append(line)
    out.append(col("Ctrl+C để dừng | dừng tool không tắt game", "d"))
    out.insert(1, key_line())
    return "\n".join(out)


def write_status(snaps, root, started):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "ts": time.time(), "root": root, "started": started, "instances": snaps,
        }), "utf-8")
        tmp.replace(STATUS_FILE)
    except Exception:
        pass


# ───────────────────────── Commands ─────────────────────────
def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def read_pid():
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def cmd_scan(a):
    Shell.detect(load_cfg()["settings"]["use_root"])
    pk = scan_packages()
    if not pk:
        print(col("Không thấy bản Roblox nào.", "y"))
        return
    for p in pk:
        print(" •", p)


def cmd_add(a):
    cfg = load_cfg()
    Shell.detect(cfg["settings"]["use_root"])
    if not str(a.place).isdigit():
        sys.exit(col("Place ID phải là số, tổng tài duck.", "r"))
    if a.auto:
        pkgs = scan_packages()
    elif a.pkg:
        pkgs = [a.pkg]
    else:
        pkgs = scan_packages()[:1] or ["com.roblox.client"]
    if not pkgs:
        sys.exit(col("Không tìm thấy package nào.", "r"))
    existing = {i["package"]: i for i in cfg["instances"]}
    for pkg in pkgs:
        if pkg in existing:
            existing[pkg]["place_id"] = str(a.place)
            if a.link is not None:
                existing[pkg]["link_code"] = a.link
            print(col("Cập nhật", "y"), pkg)
            continue
        name = a.name if (a.name and len(pkgs) == 1) else "acc%d" % (len(cfg["instances"]) + 1)
        cfg["instances"].append({
            "name": name, "package": pkg, "place_id": str(a.place),
            "link_code": a.link or "", "enabled": True,
        })
        print(col("Đã thêm", "g"), name, pkg)
    save_cfg(cfg)


def cmd_list(a):
    cfg = load_cfg()
    if not cfg["instances"]:
        print("Chưa có cửa sổ nào. Dùng: add --auto --place ID")
    for i in cfg["instances"]:
        print(" %s %-8s %-26s ID:%s %s" % (
            col("●", "g") if i.get("enabled", True) else col("○", "d"),
            i["name"], i["package"], i["place_id"],
            ("link:" + i["link_code"]) if i.get("link_code") else ""))
    print(col("\nSettings:", "B"))
    for k, v in cfg["settings"].items():
        print("  %-22s %s" % (k, v))


def cmd_remove(a):
    cfg = load_cfg()
    before = len(cfg["instances"])
    cfg["instances"] = [i for i in cfg["instances"]
                        if i["name"] != a.name and i["package"] != a.name]
    save_cfg(cfg)
    print("Đã xoá" if len(cfg["instances"]) < before else "Không thấy cửa sổ đó")


def cmd_set(a):
    cfg = load_cfg()
    if a.key not in DEFAULT_SETTINGS:
        sys.exit("Key hợp lệ: " + ", ".join(DEFAULT_SETTINGS))
    d = DEFAULT_SETTINGS[a.key]
    v = a.value
    if isinstance(d, bool):
        v = v.lower() in ("1", "true", "yes", "on")
    elif isinstance(d, int):
        v = int(v)
    cfg["settings"][a.key] = v
    save_cfg(cfg)
    print("OK:", a.key, "=", v)


def cmd_join(a):
    cfg = load_cfg()
    Shell.detect(cfg["settings"]["use_root"])
    pkg = a.package or (scan_packages() or ["com.roblox.client"])[0]
    ok, info = launch_game(pkg, a.place, a.link or "")
    print(col("Đã gửi lệnh vào game", "g") if ok else col("Lỗi: " + info, "r"))


def cmd_start(a):
    cfg = load_cfg()
    s = cfg["settings"]
    old = read_pid()
    if old and pid_alive(old) and old != os.getpid():
        sys.exit(col("Tool đang chạy (PID %d). Dùng 'stop' trước." % old, "r"))
    Shell.detect(s["use_root"])
    insts = [i for i in cfg["instances"] if i.get("enabled", True)]
    if a.names:
        want = set(a.names)
        insts = [i for i in insts if i["name"] in want or i["package"] in want]
    if a.place:
        for i in insts:
            i["place_id"] = str(a.place)
    if not insts:
        sys.exit(col("Chưa có cửa sổ nào. Dùng: add --auto --place ID", "r"))
    if shutil.which("termux-wake-lock"):
        subprocess.run(["termux-wake-lock"], capture_output=True)

    APP_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    stop_evt = threading.Event()
    for sg in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sg, lambda *_: stop_evt.set())

    workers = [Instance(i, s, stop_evt, delay=n * int(s["stagger"]), fresh=a.fresh)
               for n, i in enumerate(insts)]
    for w in workers:
        w.start()
    started = time.time()
    log_event("=== START %d cửa sổ, root=%s ===" % (len(workers), Shell.root))
    sys.stdout.write("\033[?25l")
    try:
        while not stop_evt.wait(1):
            snaps = [w.snapshot() for w in workers]
            write_status(snaps, Shell.root, started)
            sys.stdout.write("\033[H\033[J" + render(snaps, Shell.root, started) + "\n")
            sys.stdout.flush()
    finally:
        stop_evt.set()
        sys.stdout.write("\033[?25h\n")
        for w in workers:
            w.join(timeout=3)
        write_status([w.snapshot() for w in workers], Shell.root, started)
        try:
            PID_FILE.unlink()
        except Exception:
            pass
        log_event("=== STOP ===")
        print(col("Đã dừng tool.", "y"))


def cmd_status(a):
    def show():
        try:
            d = json.loads(STATUS_FILE.read_text("utf-8"))
        except Exception:
            return col("Chưa có dữ liệu. Tool chưa chạy lần nào.", "y")
        pid = read_pid()
        if not (pid and pid_alive(pid)) or time.time() - d["ts"] > 10:
            return col("Tool KHÔNG chạy. Số liệu lần cuối:\n", "r") + \
                render(d["instances"], d["root"], d["started"])
        return render(d["instances"], d["root"], d["started"])

    if not a.watch:
        print(show())
        return
    try:
        while True:
            sys.stdout.write("\033[H\033[J" + show() + "\n")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        pass


def cmd_stop(a):
    pid = read_pid()
    if not pid or not pid_alive(pid):
        print("Tool không chạy.")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not pid_alive(pid):
            break
        time.sleep(0.3)
    print(col("Đã dừng.", "g"))


def main():
    ap = argparse.ArgumentParser(description="Roblox Auto Rejoin - Termux")
    sp = ap.add_subparsers(dest="cmd")

    sp.add_parser("scan").set_defaults(fn=cmd_scan)

    p = sp.add_parser("add")
    p.add_argument("--place", required=True)
    p.add_argument("--pkg")
    p.add_argument("--name")
    p.add_argument("--link", default=None, help="linkCode private server")
    p.add_argument("--auto", action="store_true", help="thêm mọi bản Roblox tìm thấy")
    p.set_defaults(fn=cmd_add)

    sp.add_parser("list").set_defaults(fn=cmd_list)

    p = sp.add_parser("remove")
    p.add_argument("name")
    p.set_defaults(fn=cmd_remove)

    p = sp.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")
    p.set_defaults(fn=cmd_set)

    p = sp.add_parser("join")
    p.add_argument("place")
    p.add_argument("-p", "--package")
    p.add_argument("--link")
    p.set_defaults(fn=cmd_join)

    p = sp.add_parser("start")
    p.add_argument("--place")
    p.add_argument("--fresh", action="store_true", help="force-stop trước khi vào")
    p.add_argument("names", nargs="*", help="chỉ chạy các cửa sổ này")
    p.set_defaults(fn=cmd_start)

    p = sp.add_parser("status")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(fn=cmd_status)

    sp.add_parser("stop").set_defaults(fn=cmd_stop)

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        ap.print_help()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
