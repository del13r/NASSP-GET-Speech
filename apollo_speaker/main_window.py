"""Main window."""
from __future__ import annotations

import time
import os

from PySide6.QtCore import QByteArray, QMetaObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .audio import Player
from .bridge import Bridge
from .chat import ChatDelegate, ChatModel
from .clock import GetClock
from .config import PLAYBACK_SEQUENTIAL, PLAYBACK_TIMESTAMP, Config, resolve_transcript_path
from .dialogs import SettingsDialog, VoiceDialog
from .engine import EngineHost
from .getutil import format_countdown, format_get, normalize_get, parse_ts
from .pipe import CONNECTED, DISCONNECTED, WAITING, PauseReader, PipeReader, SimulatedReader
from .scheduler import Clip
from .site_sync import SiteSyncView

SITE_SETTLE_SECONDS = 0.3
from .speakers import portrait, speaker_color
from .transcript import Event, discover_transcripts, load_transcript, match_speaker
from .tts import wav_duration

UP_NEXT_COUNT = 5


class UpNextCard(QFrame):
    def __init__(self, big: bool):
        super().__init__()
        self.setObjectName("card")
        self.big = big
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        self.pic = QLabel()
        self.pic.setFixedSize(52 if big else 36, 52 if big else 36)
        self.pic.setScaledContents(True)
        body = QVBoxLayout()
        top = QHBoxLayout()
        self.name = QLabel()
        self.name.setStyleSheet("font-weight:600")
        self.eta = QLabel()
        self.eta.setObjectName("dim")
        top.addWidget(self.name, 1)
        top.addWidget(self.eta)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        if big:
            self.text.setStyleSheet("font-size:12pt")
        body.addLayout(top)
        body.addWidget(self.text)
        lay.addWidget(self.pic, 0, Qt.AlignTop)
        lay.addLayout(body, 1)
        if big:
            self.setStyleSheet(f"QFrame#card {{ border: 1.5px solid {theme.ACCENT}; }}")

    def set_event(self, ev: Event | None) -> None:
        self.setVisible(ev is not None)
        if ev is None:
            return
        u = ev.main
        canonical = match_speaker(u.label)
        dpr = self.devicePixelRatioF()
        self.pic.setPixmap(portrait(u.label, canonical, self.pic.width(), dpr))
        self.name.setText(u.label)
        self.name.setStyleSheet(f"font-weight:600;color:{speaker_color(u.label, canonical).name()}")
        text = u.text
        if len(text) > (220 if self.big else 110):
            text = text[: (217 if self.big else 107)] + "..."
        extra = len(ev.pao)
        self.text.setText(text + (f"  (+{extra} more)" if extra else ""))


class UpNextPanel(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QLabel("UP NEXT")
        head.setObjectName("heading")
        lay.addWidget(head)
        self.cards = [UpNextCard(big=(i == 0)) for i in range(UP_NEXT_COUNT)]
        for c in self.cards:
            lay.addWidget(c)
            c.hide()
        self.empty = QLabel("No upcoming events")
        self.empty.setObjectName("dim")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self._events: list[Event] = []

    def set_events(self, events: list[Event]) -> None:
        self._events = events
        for card, ev in zip(self.cards, events + [None] * UP_NEXT_COUNT):
            card.set_event(ev)
        self.empty.setVisible(not events)

    def update_times(self, est: float | None, accel: float) -> None:
        for card, ev in zip(self.cards, self._events):
            if est is None:
                card.eta.setText(ev.ts)
            else:
                card.eta.setText(f"in {format_countdown(ev.sec - est)[:-2]}   {ev.ts}")


class TranscriptLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            self.loaded.emit(load_transcript(self.path))
        except FileNotFoundError:
            self.failed.emit(f"Transcript not found: {self.path}")
        except Exception as exc:
            self.failed.emit(f"Could not read transcript ({type(exc).__name__}: {exc})")


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


class MainWindow(QMainWindow):
    speech_changed = Signal(bool)
    timeline_ready = Signal(object)
    pause_changed = Signal(object, float)

    def __init__(self, config: Config, simulate: tuple[str, float] | None = None):
        super().__init__()
        self.config = config
        self.simulate = simulate
        self.setWindowTitle("NASSP GET Speech")
        self.resize(1280, 820)
        if config.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(config.window_geometry.encode("ascii")))

        self.clock = GetClock()
        self.timeline: list[Event] = []
        self.conn_state = DISCONNECTED
        self.conn_detail = "Starting..."
        self._last_target = None
        self._last_sample_get = None
        self._loader: TranscriptLoader | None = None
        self._load_generation = 0
        self._load_pending = False
        self.reader = None
        self.pause_reader = None
        self._pause_state: bool | None = False if simulate else None
        self._pause_event_t = 0.0
        self._last_sample_t = 0.0
        self._get_invalidated_t = 0.0
        self._pause_seen_t = 0.0
        self._awaiting_running_sample = not bool(simulate)
        self._closing = False
        self.last_error = ""
        self.last_error_at = 0.0

        self.player = Player(policy=config.playback_policy, pao_override=True, parent=self)
        self.host = EngineHost(config)
        eng = self.host.engine
        eng.fired.connect(self._on_fired)
        eng.rewound.connect(self._on_rewound)
        eng.error.connect(self._on_error)
        eng.busy_changed.connect(self._on_busy)
        eng.play_clip.connect(self.player.play)
        eng.stop_audio_requested.connect(self.player.stop_all)
        self.player.playback_error.connect(self._on_error)
        self.speech_changed.connect(eng.set_speech)
        self.timeline_ready.connect(eng.set_timeline)
        self.pause_changed.connect(eng.on_pause)
        self.player.active_changed.connect(self._on_active)
        self.busy = 0
        self._site_running = False
        self._site_due: float | None = None
        self._resync_seq = 0
        self.bridge = Bridge()
        self.bridge.start()

        self._build_ui()
        self.host.start()
        self._start_reader()
        if not simulate:
            self.pause_reader = PauseReader(self)
            self.pause_reader.pause_changed.connect(self._on_pause)
            self.pause_reader.pause_seen.connect(self._on_pause_seen)
            self.pause_reader.start()

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self._tick)
        self.ui_timer.start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.stack = QStackedWidget()
        self.stack.addWidget(root)
        self.site_dock = SiteSyncView(self.bridge.port, self.config.site_offset_seconds)
        self.stack.addWidget(self.site_dock)
        self.stack.setCurrentIndex(1)
        self.setCentralWidget(self.stack)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 10)
        outer.setSpacing(12)

        header = _card()
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 14, 20, 14)
        left = QVBoxLayout()
        cap = QLabel("NEXT EVENT IN")
        cap.setObjectName("heading")
        self.countdown = QLabel("--:--.-")
        font = QFont("Cascadia Mono", 44)
        font.setStyleHint(QFont.Monospace)
        font.setBold(True)
        self.countdown.setFont(font)
        self.sub = QLabel("")
        self.sub.setObjectName("dim")
        left.addWidget(cap)
        left.addWidget(self.countdown)
        left.addWidget(self.sub)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        self.pill = QLabel()
        self.pill.setAlignment(Qt.AlignRight)
        self.get_label = QLabel("GET --")
        self.get_label.setAlignment(Qt.AlignRight)
        self.get_label.setStyleSheet("font-size:16pt;font-weight:600")
        self.accel_label = QLabel("")
        self.accel_label.setObjectName("dim")
        self.accel_label.setAlignment(Qt.AlignRight)
        right.addWidget(self.pill)
        right.addWidget(self.get_label)
        right.addWidget(self.accel_label)
        h.addLayout(right)
        outer.addWidget(header)

        controls = QHBoxLayout()
        self.transcript_button = QPushButton("Transcript")
        self.transcript_menu = QMenu(self.transcript_button)
        self.transcript_button.setMenu(self.transcript_menu)
        self.transcript_menu.aboutToShow.connect(self._refresh_transcripts)
        self._refresh_transcripts()
        controls.addWidget(self.transcript_button)
        self.speech_btn = QPushButton()
        self.speech_btn.setObjectName("speech")
        self.speech_btn.setCheckable(True)
        self.speech_btn.setChecked(self.config.speech_enabled)
        self.speech_btn.setMinimumWidth(150)
        self.speech_btn.setToolTip("Toggle speech generation and playback (F9 / Scroll Lock)")
        self.speech_btn.toggled.connect(self._speech_toggled)
        self._update_speech_text()
        controls.addWidget(self.speech_btn)

        self.policy = QComboBox()
        self.policy.addItem("Play at timestamp (may overlap)", PLAYBACK_TIMESTAMP)
        self.policy.addItem("Queue (one speaker at a time)", PLAYBACK_SEQUENTIAL)
        self.policy.setCurrentIndex(0 if self.config.playback_policy == PLAYBACK_TIMESTAMP else 1)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        controls.addWidget(self.policy)

        controls.addStretch(1)

        self.follow_box = QCheckBox("Follow current")
        self.follow_box.setChecked(self.config.follow_current)
        self.follow_box.toggled.connect(self._follow_toggled)
        controls.addWidget(self.follow_box)
        voices = QPushButton("Voices...")
        voices.clicked.connect(self._open_voices)
        settings = QPushButton("Settings...")
        settings.clicked.connect(self._open_settings)
        controls.addWidget(voices)
        controls.addWidget(settings)
        outer.addLayout(controls)
        find_btn = QPushButton("Find...")
        find_btn.clicked.connect(self._open_find)
        controls.addWidget(find_btn)

        self.site_dock.back_requested.connect(lambda: self._show_view(0))
        self.site_dock.offset_changed.connect(self._site_offset_changed)
        site_btn = QPushButton("Apollo RT")
        site_btn.setToolTip("Browser sync page: opens apolloinrealtime.org (Apollo 11, 13 or 17); unloads the transcript")
        site_btn.clicked.connect(lambda: self._show_view(1))
        controls.addWidget(site_btn)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#4a2326;border:1px solid #ff6b6b;border-radius:8px;padding:8px 12px")
        self.banner.hide()
        outer.addWidget(self.banner)

        splitter = QSplitter(Qt.Horizontal)

        self.up_next = UpNextPanel()
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 8, 0)
        sl.addWidget(self.up_next)
        splitter.addWidget(side)

        self.model = ChatModel(self.config, self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(ChatDelegate(self.view))
        self.view.setSelectionMode(QListView.NoSelection)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.setLayoutMode(QListView.SinglePass)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setFocusPolicy(Qt.NoFocus)
        splitter.addWidget(self.view)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([380, 860])
        outer.addWidget(splitter, 1)

        self.status_conn = QLabel()
        self.status_busy = QLabel()
        self.status_err = QLabel()
        sb = self.statusBar()
        sb.addWidget(self.status_conn)
        sb.addWidget(self.status_busy)
        sb.addWidget(self.status_err, 1)

        for key in (Qt.Key_F9, Qt.Key_ScrollLock):
            QShortcut(QKeySequence(key), self, activated=self.speech_btn.toggle)
        self._refresh_pill()

    # ------------------------------------------------------------ controls
    def _show_view(self, index: int) -> None:
        """The sync page keeps the transcript unloaded (no speech work); opening the transcript loads it."""
        self.stack.setCurrentIndex(index)
        if index == 1:
            self._load_generation += 1
            self._load_pending = False
            self.player.stop_all()
            self.timeline = []
            self.model.set_timeline([])
            self.up_next.set_events([])
            self.timeline_ready.emit([])
            self.banner.hide()
            self._last_target = None
        elif not self.timeline:
            self._refresh_transcripts()
            self._load_transcript()

    def _refresh_transcripts(self) -> None:
        selected = os.path.normcase(os.path.abspath(resolve_transcript_path(self.config.transcript_file)))
        transcripts = discover_transcripts()
        if not any(os.path.normcase(os.path.abspath(resolve_transcript_path(path))) == selected
                   for _, path in transcripts):
            transcripts.append((f"Custom: {self.config.transcript_file}", self.config.transcript_file))
        self.transcript_menu.clear()
        for label, path in transcripts:
            action = self.transcript_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(os.path.normcase(os.path.abspath(resolve_transcript_path(path))) == selected)
            action.setData(path)
            action.triggered.connect(lambda checked=False, path=path: self._select_transcript(path))
            if action.isChecked():
                self.transcript_button.setToolTip(f"Current transcript: {label}\nSelect a transcript")

    def _select_transcript(self, path: str) -> None:
        if path == self.config.transcript_file:
            return
        self.config.transcript_file = path
        self.config.save()
        self._refresh_transcripts()
        if self.stack.currentIndex() == 0:
            self._load_transcript()

    def _update_speech_text(self) -> None:
        on = self.speech_btn.isChecked()
        self.speech_btn.setText("\U0001F50A  Speech ON" if on else "\U0001F507  Speech OFF")

    def _speech_toggled(self, on: bool) -> None:
        self.config.speech_enabled = on
        self._update_speech_text()
        if not on:
            self.player.stop_all()
        self.speech_changed.emit(on)
        self.config.save()

    def _policy_changed(self) -> None:
        self.config.playback_policy = self.policy.currentData()
        self.player.set_policy(self.config.playback_policy)
        self.config.save()

    def _follow_toggled(self, on: bool) -> None:
        self.config.follow_current = on
        self._last_target = None
        self.config.save()

    def _open_voices(self) -> None:
        def play(path: str) -> None:
            self.player.play(Clip(-1, 0, path, "", False, False, wav_duration(path)))

        dlg = VoiceDialog(self.config, play, self)
        if dlg.exec():
            self.config.voice_map = dlg.collect()
            self.config.save()
            self.view.viewport().update()

    def _open_settings(self) -> None:
        old = (self.config.transcript_file, self.config.pipe_name)
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            dlg.apply_to(self.config)
            self.config.save()
            self._refresh_transcripts()
            if self.config.transcript_file != old[0] and self.stack.currentIndex() == 0:
                self._load_transcript()
            if self.config.pipe_name != old[1]:
                self._start_reader()

    def _open_find(self):
        from .find_dialog import FindDialog
        est = self.clock.estimate(time.monotonic()) or 0.0
        dlg = FindDialog(self.timeline, est, self)
        if dlg.exec():
            idx = dlg.selected_index()
            if idx is not None:
                self._jump_to_event(idx)

    def _jump_to_event(self, idx: int):
        self.model.set_current(idx)
        row = self.model.row_for_event(idx)
        if row >= 0:
            index = self.model.index(row)
            self.view.scrollTo(index, QListView.PositionAtCenter)

    # ---------------------------------------------------------- transcript
    def _load_transcript(self) -> None:
        self._load_generation += 1
        self._load_pending = True
        self.player.stop_all()
        self.timeline = []
        self.model.set_timeline([])
        self.up_next.set_events([])
        self.timeline_ready.emit([])
        self._last_target = None
        self.banner.setText("Loading transcript...")
        self.banner.setStyleSheet("background:#1f2d44;border:1px solid #4c9ffe;border-radius:8px;padding:8px 12px")
        self.banner.show()
        if self._loader is None:
            self._start_transcript_load()

    def _start_transcript_load(self) -> None:
        self._load_pending = False
        generation = self._load_generation
        self._loader = TranscriptLoader(str(resolve_transcript_path(self.config.transcript_file)), self)
        self._loader.loaded.connect(lambda events: self._transcript_loaded_for(generation, events))
        self._loader.failed.connect(lambda msg: self._transcript_failed_for(generation, msg))
        self._loader.finished.connect(self._transcript_load_finished)
        self._loader.start()

    def _transcript_loaded_for(self, generation: int, events: list[Event]) -> None:
        if generation == self._load_generation and not self._closing:
            self._transcript_loaded(events)

    def _transcript_failed_for(self, generation: int, msg: str) -> None:
        if generation == self._load_generation and not self._closing and self.stack.currentIndex() == 0:
            self._transcript_failed(msg)

    def _transcript_load_finished(self) -> None:
        self._loader = None
        if self._load_pending and not self._closing and self.stack.currentIndex() == 0:
            self._start_transcript_load()

    def _transcript_loaded(self, events: list[Event]) -> None:
        if self.stack.currentIndex() == 1:
            return  # site view is open: keep the transcript unloaded
        self.timeline = events
        self.model.set_timeline(events)
        self.timeline_ready.emit(events)
        self.banner.hide()
        self._last_target = None
        self.status_err.setText("")

    def _transcript_failed(self, msg: str) -> None:
        self.banner.setStyleSheet("background:#4a2326;border:1px solid #ff6b6b;border-radius:8px;padding:8px 12px")
        self.banner.setText(f"{msg}. Choose another transcript or browse in Settings.")
        self.banner.show()

    # ---------------------------------------------------------------- pipe
    def _start_reader(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait(3000)
        if self.simulate:
            self.reader = SimulatedReader(self.simulate[0], self.simulate[1], self)
        else:
            self.reader = PipeReader(self.config.pipe_name, self)
        self.reader.sample.connect(self._on_sample)
        self.reader.sample.connect(self.host.engine.on_sample)
        self.reader.status.connect(self._on_status)
        self.reader.start()

    def _on_sample(self, raw_get: str, accel: float, t: float) -> None:
        try:
            sec = parse_ts(normalize_get(raw_get))
        except ValueError:
            return
        if t < self._last_sample_t:
            return
        self._last_sample_t = t
        prev = self._last_sample_get
        self._last_sample_get = sec
        jumped = self.clock.forward_jump(sec, accel, t)
        if self.clock.update(sec, accel, t) or jumped:
            self.model.set_current(-1)
            self._last_target = None
            self._site_due = t + SITE_SETTLE_SECONDS
            if prev is not None:
                kind = "went backwards" if sec < prev else "jumped forward"
                self._show_error(
                    f"GET {kind} ({format_get(prev)} -> {format_get(sec)}); "
                    "speech resynchronised to the new position",
                    transient=True,
                )
        if self._pause_state is False and self._awaiting_running_sample and t >= self._pause_event_t:
            self._awaiting_running_sample = False
            self.clock.set_paused(False, t)
        self._publish_site(time.monotonic())

    def _on_pause(self, paused: bool | None, t: float) -> None:
        if t < self._pause_event_t:
            return
        previous = self._pause_state
        self._pause_state = paused
        self._pause_event_t = t
        if paused is not None:
            self._pause_seen_t = max(self._pause_seen_t, t)
        self.pause_changed.emit(paused, t)
        if paused is True:
            self._awaiting_running_sample = True
            self.clock.set_paused(True, t)
        elif paused is None:
            self._awaiting_running_sample = True
            self.clock.set_paused(False, t)
        elif previous is not False:
            self._awaiting_running_sample = self._last_sample_t < t
            self.clock.set_paused(self._awaiting_running_sample, max(t, self._last_sample_t))
        self._publish_site(time.monotonic())

    def _on_pause_seen(self, t: float) -> None:
        self._pause_seen_t = max(self._pause_seen_t, t)
        self._publish_site(time.monotonic())

    def _on_status(self, state: str, detail: str) -> None:
        if state == DISCONNECTED and self.conn_state != DISCONNECTED:
            self._get_invalidated_t = time.monotonic()
        self.conn_state, self.conn_detail = state, detail
        self._refresh_pill()
        self._publish_site(time.monotonic())

    def _refresh_pill(self) -> None:
        color, label = {
            CONNECTED: ("#3ddc84", "Connected"),
            WAITING: ("#ffc857", "Waiting for data"),
            DISCONNECTED: ("#ff6b6b", "Disconnected"),
        }[self.conn_state]
        self.pill.setText(f'<span style="color:{color}">\u25CF</span> {label}')
        self.status_conn.setText(self.conn_detail)

    # --------------------------------------------------------- engine events
    def _on_fired(self, idx: int) -> None:
        self.model.set_current(idx)

    def _on_rewound(self, pointer: int) -> None:
        self.model.set_current(-1)
        self._last_target = None

    def _on_error(self, msg: str) -> None:
        self._show_error(msg)

    def _show_error(self, msg: str, transient: bool = False) -> None:
        self.last_error = msg
        self.last_error_at = time.monotonic()
        color = "#ffc857" if transient else "#ff6b6b"
        self.status_err.setText(f'<span style="color:{color}">{msg}</span>')

    def _on_busy(self, n: int) -> None:
        self.busy = n
        self.status_busy.setText(f"Generating speech ({n})  " if n else "")

    def _on_active(self, active: set) -> None:
        self.model.set_speaking({a for a in active if a >= 0})

    # ----------------------------------------------------------------- tick
    def _tick(self) -> None:
        now = time.monotonic()
        est = self.clock.estimate(now)
        accel = self.clock.accel
        self._site_tick(now, est, self.clock.is_stale(now), accel)
        if est is None or not self.timeline:
            self.countdown.setText("--:--.-")
            self.countdown.setStyleSheet(f"color:{theme.TEXT_DIM}")
            self.sub.setText("Waiting for GET from Orbiter..." if est is None else "Transcript not loaded")
            return

        stale = self.clock.is_stale(now)
        nxt = self.model.next_for(est)
        if nxt != self.model.next_idx:
            self.model.set_next(nxt)
            self.up_next.set_events(self.timeline[nxt : nxt + UP_NEXT_COUNT])
        elif self.up_next._events == [] and nxt < len(self.timeline):
            self.up_next.set_events(self.timeline[nxt : nxt + UP_NEXT_COUNT])
        self.up_next.update_times(est, accel)

        self.get_label.setText(f"GET {format_get(est)}")
        self.accel_label.setText(f"time acceleration {accel:g}x")
        if nxt >= len(self.timeline):
            self.countdown.setText("END")
            self.countdown.setStyleSheet(f"color:{theme.TEXT_DIM}")
            self.sub.setText("No more events in the transcript")
        else:
            delta = self.timeline[nxt].sec - est
            real = delta / accel if accel > 0 else float("inf")
            color = QColor(theme.TEXT_DIM) if stale else theme.countdown_color(min(real, 1e6))
            self.countdown.setText(format_countdown(delta))
            self.countdown.setStyleSheet(f"color:{color.name()}")
            nxt_ev = self.timeline[nxt]
            extra = f"  \u00b7  \u2248 {real:.1f}s real time" if abs(accel - 1.0) > 0.01 and accel > 0 else ""
            state = "  \u00b7  no GET updates (paused or disconnected)" if stale else ""
            self.sub.setText(f"{nxt_ev.ts}  \u00b7  {nxt_ev.main.label}{extra}{state}")

        if self.config.follow_current:
            target = self.model.current if self.model.current >= 0 else nxt
            if target != self._last_target:
                row = self.model.row_for_event(target)
                if row >= 0:
                    index = self.model.index(row)
                    self.view.scrollTo(index, QListView.PositionAtCenter)
                    # Batched layout may not have positioned distant rows yet; retry next tick.
                    if self.view.visualRect(index).intersects(self.view.viewport().rect()):
                        self._last_target = target
                else:
                    self._last_target = target

    # ------------------------------------------------------- site sync
    def _publish_site(self, now: float) -> None:
        self._site_tick(now, self.clock.estimate(now), self.clock.is_stale(now), self.clock.accel)

    def _site_tick(self, now: float, est: float | None, stale: bool, accel: float) -> None:
        available = (est is not None and self._pause_state is not None
                     and self.conn_state != DISCONNECTED
                     and self._last_sample_t >= self._get_invalidated_t
                     and (self.simulate or self._pause_seen_t >= self._get_invalidated_t))
        if self._pause_state is False:
            available = (available and self.conn_state == CONNECTED
                         and not stale and not self._awaiting_running_sample)
        running = available and self._pause_state is False and abs(accel - 1.0) < 0.01
        if running and not self._site_running:
            self._resync_seq += 1
        if not running:
            self._site_due = None
        self._site_running = running
        if self._site_due is not None and now >= self._site_due:
            self._site_due = None
            self._resync_seq += 1
        self.bridge.update(
            est, accel, running, self._resync_seq, self.config.site_offset_seconds,
            self._pause_state if available else None,
        )

    def _site_offset_changed(self, value: float) -> None:
        self.config.site_offset_seconds = value
        self.config.save()

    # ------------------------------------------------------------- shutdown
    def closeEvent(self, event) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.ui_timer.stop()
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait(3000)
        if self.pause_reader is not None:
            self.pause_reader.stop()
            self.pause_reader.wait(3000)
        if self._loader is not None:
            self._loader.wait(5000)
        self.player.stop_all()
        self.bridge.stop()
        self.host.stop()
        self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.config.save()
        event.accept()
