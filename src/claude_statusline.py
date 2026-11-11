"""Claude Code status line that also records plan rate limits for the wallpaper.

Claude Code runs this command and sends a JSON document on stdin. When the
account has plan limits, the payload carries ``rate_limits`` (five-hour and
seven-day windows). We save only those percentages and reset times to a local
file that pack_midia_control.py serves; nothing else from the payload is kept
and nothing is sent over the network.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def limits_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "pack-midia-control" / "claude-limits.json"


def extract_limits(payload: dict, now: float | None = None) -> dict | None:
    """Return the rate-limit windows from a status line payload, or None."""
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return None
    out = {"saved_at": now if now is not None else time.time()}
    for name in ("five_hour", "seven_day"):
        window = limits.get(name)
        if isinstance(window, dict):
            out[name] = {
                "used_percentage": window.get("used_percentage"),
                "resets_at": window.get("resets_at"),
            }
    return out if len(out) > 1 else None


def save_limits(data: dict, path: Path | None = None) -> None:
    path = path or limits_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render(payload: dict, limits: dict | None) -> str:
    model = ((payload.get("model") or {}).get("display_name") or "Claude") if isinstance(payload, dict) else "Claude"
    parts = [str(model)]
    if limits:
        def pct(name):
            value = (limits.get(name) or {}).get("used_percentage")
            return f"{float(value):.0f}%" if isinstance(value, (int, float)) else "--"
        parts.append(f"5h {pct('five_hour')} · 7d {pct('seven_day')}")
    return " | ".join(parts)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    limits = extract_limits(payload)
    if limits:
        try:
            save_limits(limits)
        except Exception:
            pass
    # Write bytes: the Windows console code page is not UTF-8.
    sys.stdout.buffer.write(render(payload, limits).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
