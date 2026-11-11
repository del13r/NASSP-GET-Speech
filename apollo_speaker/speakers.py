"""Speaker appearance: colours and portraits."""
from __future__ import annotations

import colorsys
import hashlib
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPixmap

from .transcript import PAO

PORTRAIT_FILES = {
    "Conrad": "conrad.png",
    "Gordon": "gordon.png",
    "Bean": "bean.png",
    "Gibson": "gibson.png",
    "Carr": "carr.png",
    "Lind": "lind.png",
    "Weitz": "Weitz.png",
    "Stafford": "stafford.png",
    "Schmitt": "scmitt.png",
}

SPEAKER_COLORS = {
    "Conrad": "#4FC3F7",
    "Gordon": "#81C784",
    "Bean": "#FFB74D",
    "Carr": "#BA68C8",
    "Gibson": "#F06292",
    PAO: "#B0BEC5",
    "Lind": "#4DB6AC",
    "Weitz": "#FFD54F",
    "Stafford": "#9575CD",
    "Schmitt": "#A1887F",
}


def assets_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "assets"


def speaker_color(label: str, canonical: str | None) -> QColor:
    if canonical and canonical in SPEAKER_COLORS:
        return QColor(SPEAKER_COLORS[canonical])
    h = int(hashlib.md5(label.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.95)
    return QColor.fromRgbF(r, g, b)


def initials(label: str) -> str:
    words = [w for w in label.replace(".", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


_cache: dict[tuple, QPixmap] = {}


def portrait(label: str, canonical: str | None, size: int, dpr: float = 1.0) -> QPixmap:
    """Circular portrait, or a coloured initials avatar when no picture is available."""
    key = (label if canonical is None else canonical, size, dpr)
    if key in _cache:
        return _cache[key]
    px_size = int(size * dpr)
    out = QPixmap(px_size, px_size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, px_size, px_size))
    p.setClipPath(clip)

    src = QPixmap()
    fname = PORTRAIT_FILES.get(canonical or "")
    if fname:
        src = QPixmap(str(assets_dir() / "portraits" / fname))
    if not src.isNull():
        side = min(src.width(), src.height())
        top = int((src.height() - side) * 0.15)  # bias towards the face
        src = src.copy((src.width() - side) // 2, top, side, side)
        p.drawPixmap(0, 0, src.scaled(px_size, px_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        color = speaker_color(label, canonical)
        p.fillRect(out.rect(), QBrush(color.darker(180)))
        p.setPen(color.lighter(140))
        font = QFont("Segoe UI", max(6, int(px_size * 0.34)))
        font.setBold(True)
        p.setFont(font)
        text = "PAO" if canonical == PAO else initials(label)
        p.drawText(out.rect(), Qt.AlignCenter, text)
    p.end()
    out.setDevicePixelRatio(dpr)
    _cache[key] = out
    return out
