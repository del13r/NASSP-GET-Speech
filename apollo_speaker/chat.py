"""Chat-style transcript model and delegate."""
from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyledItemDelegate

from . import theme
from .speakers import portrait, speaker_color
from .transcript import Event, match_speaker

RowRole = Qt.UserRole + 1

PAST, CURRENT, NEXT, UPCOMING = range(4)


class ChatModel(QAbstractListModel):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.events: list[Event] = []
        self.rows: list[tuple[int, int]] = []  # (event index, utterance index)
        self.first_row: list[int] = []
        self.secs: list[int] = []
        self.current = -1
        self.next_idx = 0
        self.speaking: set[int] = set()

    def set_timeline(self, events: list[Event]) -> None:
        self.beginResetModel()
        self.events = events
        self.secs = [e.sec for e in events]
        self.rows = []
        self.first_row = []
        for e in events:
            self.first_row.append(len(self.rows))
            for k in range(len(e.utterances)):
                self.rows.append((e.index, k))
        self.current = -1
        self.next_idx = 0
        self.speaking = set()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def event_state(self, ev_idx: int) -> int:
        if ev_idx == self.current:
            return CURRENT
        if ev_idx < self.next_idx:
            return PAST
        if ev_idx == self.next_idx:
            return NEXT
        return UPCOMING

    def side(self, speaker: str) -> str:
        return "right" if self.config.voice_for(speaker)["pan"] == "right" else "left"

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        ev_idx, k = self.rows[index.row()]
        ev = self.events[ev_idx]
        utt = ev.utterances[k]
        if role == Qt.DisplayRole:
            return f"{ev.ts} {utt.label}: {utt.text}"
        if role == RowRole:
            return {
                "ts": ev.ts,
                "first": k == 0,
                "utt": utt,
                "state": self.event_state(ev_idx),
                "speaking": ev_idx in self.speaking,
                "side": self.side(utt.speaker),
                "event": ev_idx,
            }
        return None

    def row_for_event(self, ev_idx: int) -> int:
        if not self.first_row or ev_idx < 0:
            return -1
        return self.first_row[min(ev_idx, len(self.first_row) - 1)]

    def _touch(self, lo: int, hi: int) -> None:
        if not self.rows or hi < lo:
            return
        lo = max(0, min(lo, len(self.events) - 1))
        hi = max(0, min(hi, len(self.events) - 1))
        last_row = self.first_row[hi] + len(self.events[hi].utterances) - 1
        self.dataChanged.emit(self.index(self.first_row[lo]), self.index(last_row), [RowRole])

    def set_current(self, ev_idx: int) -> None:
        old = self.current
        self.current = ev_idx
        for i in (old, ev_idx):
            if i >= 0:
                self._touch(i, i)

    def set_next(self, ev_idx: int) -> None:
        if ev_idx == self.next_idx:
            return
        old = self.next_idx
        self.next_idx = ev_idx
        lo, hi = sorted((old, ev_idx))
        self._touch(lo, hi)  # a rewind/jump changes everything between the two pointers

    def set_speaking(self, active: set[int]) -> None:
        changed = self.speaking ^ active
        self.speaking = set(active)
        for i in changed:
            self._touch(i, i)

    def next_for(self, est: float) -> int:
        return bisect_right(self.secs, est)


class ChatDelegate(QStyledItemDelegate):
    PORTRAIT = 44
    MARGIN = 14
    PAD = 12

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self._name_font = QFont("Segoe UI Semibold", 9)
        self._text_font = QFont("Segoe UI", 11)
        self._small_font = QFont("Segoe UI", 8)

    def _bubble_width(self, total: int) -> int:
        return max(220, min(int(total * 0.72), 760))

    def sizeHint(self, option, index) -> QSize:
        row = index.data(RowRole)
        if row is None:
            return QSize(0, 0)
        total = self.view.viewport().width()
        text_w = self._bubble_width(total) - 2 * self.PAD
        fm = QFontMetrics(self._text_font)
        adv = fm.horizontalAdvance(row["utt"].text)
        lines = max(1, int(adv * 1.12 / max(text_w, 1)) + 1)
        h = lines * fm.lineSpacing() + 2 * self.PAD + 20
        return QSize(total, max(h, self.PORTRAIT + 10) + 8)

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.data(RowRole)
        if row is None:
            return
        utt = row["utt"]
        state = row["state"]
        canonical = match_speaker(utt.label)
        color = speaker_color(utt.label, canonical)
        right = row["side"] == "right"
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setOpacity({PAST: 0.72, CURRENT: 1.0, NEXT: 1.0, UPCOMING: 0.55}[state])

        bubble_w = self._bubble_width(rect.width())
        pic = self.PORTRAIT
        y = rect.top() + 6
        if right:
            pic_x = rect.right() - self.MARGIN - pic
            bub_x = pic_x - 10 - bubble_w
        else:
            pic_x = rect.left() + self.MARGIN
            bub_x = pic_x + pic + 10
        bubble = QRectF(bub_x, y, bubble_w, rect.height() - 10)

        if row["first"]:
            dpr = painter.device().devicePixelRatioF()
            painter.drawPixmap(QPointF(pic_x, y), portrait(utt.label, canonical, pic, dpr))
            if state == CURRENT:
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QRectF(pic_x - 2, y - 2, pic + 4, pic + 4))

        path = QPainterPath()
        path.addRoundedRect(bubble, 14, 14)
        painter.fillPath(path, QColor(theme.PANEL_ALT))
        if state == CURRENT:
            tint = QColor(color)
            tint.setAlpha(46)
            painter.fillPath(path, tint)
            painter.setPen(QPen(color, 2))
        elif state == NEXT:
            painter.setPen(QPen(QColor(theme.ACCENT), 1.5, Qt.DashLine))
        else:
            painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        inner = bubble.adjusted(self.PAD, 8, -self.PAD, -8)
        painter.setFont(self._name_font)
        painter.setPen(color)
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), 16), Qt.AlignLeft | Qt.AlignVCenter, utt.label)
        painter.setFont(self._small_font)
        painter.setPen(QColor(theme.TEXT_DIM))
        stamp = row["ts"]
        if row["speaking"]:
            stamp = "\u25CF speaking   " + stamp
        elif state == CURRENT:
            stamp = "\u25B6 now   " + stamp
        elif state == NEXT:
            stamp = "up next   " + stamp
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), 16), Qt.AlignRight | Qt.AlignVCenter, stamp)

        painter.setFont(self._text_font)
        painter.setPen(QColor(theme.TEXT))
        text_rect = QRectF(inner.left(), inner.top() + 20, inner.width(), inner.height() - 20)
        painter.drawText(text_rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, utt.text)
        painter.restore()
