"""GET (Ground Elapsed Time) parsing and formatting helpers."""
from __future__ import annotations

import re

_PIPE_RE = re.compile(r"([+-]?\d+:\d{1,2}:\d{1,2})\s+([+-]?\d+(?:\.\d+)?)")


def normalize_get(g: str) -> str:
    sign = "-" if g.startswith("-") else "+"
    g = g.lstrip("+-")
    h, m, s = g.split(":")
    while len(h) < 3:
        h = "0" + h
    return f"{sign}{h}:{m}:{s}"


def parse_ts(ts: str) -> int:
    sign = -1 if ts.startswith("-") else 1
    ts = ts.lstrip("+-")
    h, m, s = ts.split(":")
    return sign * (int(h) * 3600 + int(m) * 60 + int(s))


def format_get(seconds: float) -> str:
    sign = "-" if seconds < 0 else "+"
    total = int(abs(seconds))
    return f"{sign}{total // 3600:03d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def format_countdown(delta: float) -> str:
    """Countdown text with one decimal place, e.g. ``0:07.3`` or ``1:02:03.0``."""
    delta = max(0.0, delta)
    tenths = int(delta * 10)
    total, t = divmod(tenths, 10)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{t}"
    return f"{m}:{s:02d}.{t}"


def parse_pipe_message(data: bytes | str) -> tuple[str, float] | None:
    """Return ``(raw_get, time_acceleration)`` from a pipe chunk, or ``None``.

    The orbiter addon sends ``GET\\naccel``. If a read returned several messages the
    most recent one wins.
    """
    if isinstance(data, bytes):
        try:
            data = data.decode("ascii", errors="ignore")
        except Exception:
            return None
    matches = _PIPE_RE.findall(data)
    if not matches:
        return None
    raw_get, accel = matches[-1]
    try:
        parse_ts(normalize_get(raw_get))
        return raw_get, float(accel)
    except ValueError:
        return None
