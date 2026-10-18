"""Loopback helper for Windows global media controls in Wallpaper Engine."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"
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
        if self.path.split("?", 1)[0] != "/health":
            self._json(404, {"ok": False, "error": "not found"}, origin)
            return
        self._json(200, {"ok": True, "commands": list(MEDIA_KEYS)}, origin)

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
