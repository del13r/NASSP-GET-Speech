"""Dark theme and countdown colour ramp."""
from __future__ import annotations

from PySide6.QtGui import QColor

BG = "#14171c"
PANEL = "#1c2027"
PANEL_ALT = "#232832"
BORDER = "#2e3440"
TEXT = "#e6e9ef"
TEXT_DIM = "#8b93a1"
ACCENT = "#4c9ffe"

# (seconds remaining, colour) - calm green far away, amber approaching, red at zero.
_RAMP = [
    (0.0, (255, 59, 48)),
    (10.0, (255, 200, 87)),
    (30.0, (124, 224, 163)),
]


def countdown_color(seconds_remaining: float) -> QColor:
    s = max(0.0, seconds_remaining)
    if s >= _RAMP[-1][0]:
        return QColor(*_RAMP[-1][1])
    for (s0, c0), (s1, c1) in zip(_RAMP, _RAMP[1:]):
        if s <= s1:
            t = (s - s0) / (s1 - s0)
            return QColor(*(round(a + (b - a) * t) for a, b in zip(c0, c1)))
    return QColor(*_RAMP[-1][1])


STYLESHEET = f"""
* {{ font-family: "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 10pt; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#heading {{ color: {TEXT_DIM}; font-size: 8.5pt; letter-spacing: 1px; }}
QPushButton, QToolButton {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked {{ background: #1f4a7a; border-color: {ACCENT}; }}
QPushButton#speech:checked {{ background: #1d5c3a; border-color: #3ddc84; }}
QPushButton#speech:!checked {{ background: #4a2326; border-color: #ff6b6b; }}
QPushButton:disabled {{ color: #5b6270; }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {PANEL_ALT}; selection-background-color: #1f4a7a; border: 1px solid {BORDER}; }}
QCheckBox {{ spacing: 8px; }}
QListView {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; outline: none; }}
QTableWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BORDER}; }}
QHeaderView::section {{ background: {PANEL_ALT}; color: {TEXT_DIM}; border: none; padding: 6px; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a4252; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""
