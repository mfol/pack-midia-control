"""Loopback helper for Windows global media controls in Wallpaper Engine."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.2.0"
PORT = 47613
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

MEDIA_KEYS = {
    "previous": 0xB1,     # VK_MEDIA_PREV_TRACK
    "play-pause": 0xB3,   # VK_MEDIA_PLAY_PAUSE
    "next": 0xB0,         # VK_MEDIA_NEXT_TRACK
}

# Wallpaper Engine's file-backed web view sends a null origin. These loopback
# preview origins are included for local development and tests.
ALLOWED_ORIGINS = {
    "null",
    "http://127.0.0.1:8765",
    "http://127.0.0.1:8766",
    "http://localhost:8765",
    "http://localhost:8766",
}


# --- AI usage (Claude Code, Codex, ...) via ccusage -----------------------
# ccusage reads the local session logs of the coding agents. The version is
# pinned to a major so a new major cannot change what is executed unnoticed.
CCUSAGE_PACKAGE = "ccusage@20"
USAGE_CACHE_SECONDS = 120
USAGE_TIMEOUT_SECONDS = 180


def _run_ccusage(args, runner=None, timeout=USAGE_TIMEOUT_SECONDS):
    """Run ccusage through npx and return its parsed JSON output."""
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx not found; install Node.js")
    command = [npx, "-y", CCUSAGE_PACKAGE, *args, "--json"]
    runner = runner or subprocess.run
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": timeout,
        "env": {**os.environ, "NO_COLOR": "1"},
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    result = runner(command, **kwargs)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ccusage failed").strip()[-200:])
    return json.loads(result.stdout)


def summarize_usage(monthly: dict, blocks: dict | None, today: date | None = None) -> dict:
    """Reduce ccusage output to what the wallpaper card needs."""
    today = today or date.today()
    period = today.strftime("%Y-%m")
    rows = monthly.get("monthly") or []
    row = next((r for r in rows if r.get("period") == period), None) or {}
    agents = []
    for item in row.get("agents") or []:
        agents.append({
            "agent": item.get("agent"),
            "cost": round(float(item.get("totalCost") or 0), 2),
            "tokens": int(item.get("totalTokens") or 0),
            "input": int(item.get("inputTokens") or 0),
            "output": int(item.get("outputTokens") or 0),
            "models": item.get("modelsUsed") or [],
        })
    agents.sort(key=lambda a: a["cost"], reverse=True)
    block = None
    for candidate in (blocks or {}).get("blocks") or []:
        if candidate.get("isActive"):
            projection = candidate.get("projection") or {}
            burn = candidate.get("burnRate") or {}
            block = {
                "cost": round(float(candidate.get("costUSD") or 0), 2),
                "tokens": int(candidate.get("totalTokens") or 0),
                "end": candidate.get("endTime"),
                "remainingMinutes": projection.get("remainingMinutes"),
                "costPerHour": round(float(burn.get("costPerHour") or 0), 2),
            }
            break
    return {
        "period": period,
        "day": today.day,
        "cost": round(float(row.get("totalCost") or 0), 2),
        "tokens": int(row.get("totalTokens") or 0),
        "input": int(row.get("inputTokens") or 0),
        "output": int(row.get("outputTokens") or 0),
        "agents": agents,
        "block": block,
    }


class UsageCache:
    """Refreshes usage in a background thread so HTTP requests never wait on npx."""

    def __init__(self, loader=None, ttl: float = USAGE_CACHE_SECONDS, clock=time.monotonic):
        self.loader = loader or self._load
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.data = None
        self.error = None
        self.updated = None
        self.updated_at = None
        self.refreshing = False
        self.thread = None

    @staticmethod
    def _load() -> dict:
        today = date.today()
        since = today.replace(day=1).strftime("%Y%m%d")
        monthly = _run_ccusage(["monthly", "--by-agent", "--since", since])
        try:
            blocks = _run_ccusage(["blocks", "--active"])
        except Exception:
            blocks = None
        return summarize_usage(monthly, blocks, today)

    def _refresh(self) -> None:
        try:
            data = self.loader()
            with self.lock:
                self.data, self.error = data, None
                self.updated = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.refreshing = False
                self.updated_at = self.clock()

    def snapshot(self) -> dict:
        """Return cached data and start a refresh when it is stale."""
        with self.lock:
            stale = self.updated_at is None or self.clock() - self.updated_at >= self.ttl
            if stale and not self.refreshing:
                self.refreshing = True
                self.thread = threading.Thread(target=self._refresh, daemon=True)
                self.thread.start()
            if self.data is None:
                if self.error and not self.refreshing:
                    return {"ok": False, "error": self.error}
                return {"ok": False, "pending": True}
            return {"ok": True, "updated": self.updated, "refreshing": self.refreshing,
                    "stale": bool(self.error), **self.data}


USAGE = UsageCache()


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


def build_input_sequence(action: str):
    """Return key-down/key-up INPUT events for one supported media command."""
    try:
        virtual_key = MEDIA_KEYS[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported media action: {action}") from exc

    events = (INPUT * 2)()
    events[0].type = INPUT_KEYBOARD
    events[0].ki = KEYBDINPUT(virtual_key, 0, 0, 0, 0)
    events[1].type = INPUT_KEYBOARD
    events[1].ki = KEYBDINPUT(virtual_key, 0, KEYEVENTF_KEYUP, 0, 0)
    return events


def send_media_action(action: str, sender=None) -> bool:
    """Send one Windows global media-key press; sender is injectable for tests."""
    if os.name != "nt" and sender is None:
        raise OSError("Media-key injection is supported only on Windows")
    events = build_input_sequence(action)
    if sender is None:
        sender = ctypes.windll.user32.SendInput
        sender.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        sender.restype = wintypes.UINT
    sent = sender(len(events), events, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(ctypes.get_last_error(), "Windows SendInput did not send both key events")
    return True


def origin_allowed(origin: str | None) -> bool:
    """Return whether a request comes from the wallpaper or local preview."""
    return origin in ALLOWED_ORIGINS


class MediaHandler(BaseHTTPRequestHandler):
    server_version = f"PackMidiaControl/{__version__}"
    command_sender = None
    usage = USAGE

    def log_message(self, _format, *_args):
        # Do not log media titles or user activity.
        return

    def _cors(self, origin: str) -> None:
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, status: int, body: dict, origin: str | None = None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        if origin and origin_allowed(origin):
            self._cors(origin)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if not origin_allowed(origin):
            self.send_error(403)
            return
        self.send_response(204)
        self._cors(origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        origin = self.headers.get("Origin")
        if not origin_allowed(origin):
            self.send_error(403)
            return
        route = self.path.split("?", 1)[0]
        if route == "/usage":
            snapshot = self.usage.snapshot()
            ok = snapshot.get("ok") or snapshot.get("pending")
            self._json(200 if ok else 502, snapshot, origin)
            return
        if route != "/health":
            self._json(404, {"ok": False, "error": "not found"}, origin)
            return
        self._json(200, {"ok": True, "commands": list(MEDIA_KEYS), "usage": True}, origin)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not origin_allowed(origin):
            self.send_error(403)
            return
        action = self.path.split("?", 1)[0].removeprefix("/media/")
        if action not in MEDIA_KEYS:
            self._json(404, {"ok": False, "error": "unsupported action"}, origin)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024:
                self._json(413, {"ok": False, "error": "request too large"}, origin)
                return
            if length:
                self.rfile.read(length)
            send_media_action(action, sender=self.command_sender)
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)}, origin)
            return
        self._json(200, {"ok": True, "action": action}, origin)


def serve(host: str = "127.0.0.1", port: int = PORT) -> None:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("The helper must only bind to loopback")
    server = ThreadingHTTPServer((host, port), MediaHandler)
    server.daemon_threads = True
    print(f"pack-midia-control listening on http://{host}:{port} (loopback only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("pack-midia-control stopped", flush=True)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Local Windows media-key helper for Wallpaper Engine")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
