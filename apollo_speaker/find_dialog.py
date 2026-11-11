from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from .getutil import parse_ts, normalize_get, format_countdown


def format_delta(delta: float) -> str:
    if abs(delta) < 0.5:
        return "now"
    if delta > 0:
        return "in " + format_countdown(delta)
    return format_countdown(-delta) + " ago"


def colour_for_delta(delta: float) -> QColor:
    if abs(delta) < 0.5:
        return QColor("#3399ff")   # now
    if delta > 0:
        return QColor("#2ecc71")   # future
    return QColor("#e74c3c")       # past


class FindDialog(QDialog):
    def __init__(self, timeline, current_get_sec, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.current_get_sec = current_get_sec

        self.setWindowTitle("Find Event")
        self.resize(480, 360)

        lay = QVBoxLayout(self)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter text or GET timestamp (e.g. 002:12:45)")
        lay.addWidget(self.input)

        btn = QPushButton("Search")
        btn.clicked.connect(self._search)
        lay.addWidget(btn)

        self.results = QListWidget()
        lay.addWidget(self.results)

        go = QPushButton("Go to selected")
        go.clicked.connect(self.accept)
        lay.addWidget(go)

    def _add_result(self, idx, ev):
        delta = ev.sec - self.current_get_sec
        diff = format_delta(delta)
        colour = colour_for_delta(delta)

        item = QListWidgetItem(
            f"{ev.ts}  ({diff}) — {ev.main.label}: {ev.main.text}"
        )
        item.setData(Qt.UserRole, idx)
        item.setForeground(colour)
        self.results.addItem(item)

    def _search(self):
        query = self.input.text().strip()
        self.results.clear()
        if not query:
            return

        # Try timestamp search
        try:
            sec = parse_ts(normalize_get(query))
            for idx, ev in enumerate(self.timeline):
                if ev.sec == sec:
                    self._add_result(idx, ev)
                    break
        except Exception:
            pass

        # Text search
        q = query.lower()
        for idx, ev in enumerate(self.timeline):
            if q in ev.main.text.lower() or q in ev.main.label.lower():
                self._add_result(idx, ev)

        # Auto-focus on closest event to current GET
        closest_item = None
        closest_delta = float("inf")

        for i in range(self.results.count()):
            item = self.results.item(i)
            idx = item.data(Qt.UserRole)
            ev = self.timeline[idx]
            delta = abs(ev.sec - self.current_get_sec)
            if delta < closest_delta:
                closest_delta = delta
                closest_item = item

        if closest_item is not None:
            self.results.setCurrentItem(closest_item)
            self.results.scrollToItem(closest_item, QListWidget.PositionAtCenter)

    def selected_index(self):
        item = self.results.currentItem()
        if not item:
            return None
        return item.data(Qt.UserRole)
