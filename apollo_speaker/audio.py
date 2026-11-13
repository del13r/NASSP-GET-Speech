"""Audio playback (Qt thread) with the two playback policies."""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from .config import PLAYBACK_SEQUENTIAL, PLAYBACK_TIMESTAMP
from .scheduler import Clip

PAO_INTERRUPT_ACCEL = 10.0


class QtSoundBackend:
    """Starts a WAV through QSoundEffect; each clip gets its own effect so clips can overlap."""

    class _Handle:
        def __init__(self, path: str, on_error):
            from PySide6.QtMultimedia import QSoundEffect

            self.effect = QSoundEffect()
            self._on_error = on_error
            self._reported_error = False
            self.failed = False
            self.started = False
            self.finished = False
            self.effect.statusChanged.connect(self._status_changed)
            self.effect.playingChanged.connect(self._playing_changed)
            self.effect.setSource(QUrl.fromLocalFile(path))
            self.effect.setVolume(1.0)
            self.effect.play()

        def _playing_changed(self) -> None:
            if self.effect.isPlaying():
                self.started = True
            elif self.started:
                self.finished = True

        @property
        def done(self) -> bool:
            return self.failed or self.finished

        def _status_changed(self) -> None:
            from PySide6.QtMultimedia import QSoundEffect

            if self.effect.status() == QSoundEffect.Status.Error and not self._reported_error:
                self._reported_error = True
                self.failed = True
                self._on_error(f"Audio playback failed for {self.effect.source().toLocalFile()}")

        def stop(self) -> None:
            try:
                self.effect.stop()
                self.effect.setSource(QUrl())
            except Exception:
                pass
            self.effect.deleteLater()

    def __init__(self, on_error):
        self.on_error = on_error

    def start(self, path: str):
        return self._Handle(path, self.on_error)


class Player(QObject):
    active_changed = Signal(object)  # set[int] of event indexes currently audible
    playback_error = Signal(str)

    END_PAD = 0.12
    HARD_LIMIT_PAD = 15.0  # safety net if the backend never reports completion

    def __init__(self, backend=None, policy: str = PLAYBACK_TIMESTAMP, pao_override: bool = True, parent=None):
        super().__init__(parent)
        self.backend = backend or QtSoundBackend(self._report_playback_error)
        self.policy = policy
        self.pao_override = pao_override
        self._active: list[dict] = []
        self._queue: deque[Clip] = deque()
        self._null_pao_queue: deque[Clip] = deque()
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    # ---- configuration ----
    def set_policy(self, policy: str) -> None:
        if policy == self.policy:
            return
        self.policy = policy
        if policy == PLAYBACK_TIMESTAMP:
            queued = list(self._queue)
            self._queue.clear()
            for c in queued:
                self.play(c)
        else:
            if self._null_pao_queue:
                self._queue.extend(self._null_pao_queue)
                self._null_pao_queue.clear()

    def set_pao_override(self, enabled: bool) -> None:
        self.pao_override = enabled

    # ---- playback ----
    def _report_playback_error(self, message: str) -> None:
        self.playback_error.emit(message)

    def play(self, clip: Clip) -> None:
        interrupt_pao = (
            clip.is_pao
            and not clip.is_null_pao
            and self.pao_override
            and clip.accel > PAO_INTERRUPT_ACCEL
        )

        # Timestamped PAO always plays; it stops older PAO first when enabled.
        if clip.is_pao and not clip.is_null_pao and self.policy == PLAYBACK_TIMESTAMP:
            if interrupt_pao:
                for entry in [a for a in self._active if a["pao"]]:
                    self._finish(entry, notify=False)
            return self._start(clip)

        if self.policy == PLAYBACK_SEQUENTIAL:
            if interrupt_pao:
                for entry in [a for a in self._active if a["pao"]]:
                    self._finish(entry, notify=False)
                if self._active:
                    self._queue.appendleft(clip)
                else:
                    self._start(clip)
                return
            if self._active:
                self._queue.append(clip)
            else:
                self._start(clip)
            return

        if interrupt_pao:
            for entry in [a for a in self._active if a["pao"]]:
                self._finish(entry, notify=False)

        if clip.is_null_pao:
            has_active_null_pao = any(a["clip"].is_null_pao for a in self._active)
            if has_active_null_pao or self._null_pao_queue:
                self._null_pao_queue.append(clip)
                return

        self._start(clip)

    def _start(self, clip: Clip) -> None:
        try:
            handle = self.backend.start(clip.path)
        except Exception as exc:
            self._report_playback_error(f"Could not play {clip.path}: {exc}")
            self._next_queued()
            return
        entry = {
            "clip": clip,
            "handle": handle,
            "end": time.monotonic() + max(clip.duration, 0.05) + self.END_PAD,
            "limit": time.monotonic() + max(clip.duration, 0.05) + self.HARD_LIMIT_PAD,
            "pao": clip.is_pao,
        }
        self._active.append(entry)
        if not self._timer.isActive():
            self._timer.start()
        self._emit()

    def _poll(self) -> None:
        now = time.monotonic()
        for entry in [a for a in self._active if self._is_done(a, now)]:
            self._finish(entry)
        if not self._active and not self._queue and not self._null_pao_queue:
            self._timer.stop()

    @staticmethod
    def _is_done(entry: dict, now: float) -> bool:
        done = getattr(entry["handle"], "done", None)
        if done is None:
            return entry["end"] <= now
        return bool(done) or entry["limit"] <= now

    def _finish(self, entry: dict, notify: bool = True) -> None:
        if entry not in self._active:
            return
        self._active.remove(entry)
        entry["handle"].stop()
        self._emit()
        if notify:
            self._next_queued()

    def _next_queued(self) -> None:
        if self.policy == PLAYBACK_SEQUENTIAL:
            if not self._active and self._queue:
                self._start(self._queue.popleft())
        else:
            if self._null_pao_queue and not any(a["clip"].is_null_pao for a in self._active):
                self._start(self._null_pao_queue.popleft())

    def stop_all(self) -> None:
        self._queue.clear()
        self._null_pao_queue.clear()
        for entry in list(self._active):
            self._finish(entry, notify=False)
        self._emit()

    def _emit(self) -> None:
        self.active_changed.emit({a["clip"].event_index for a in self._active})

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def queued_count(self) -> int:
        return len(self._queue) + len(self._null_pao_queue)
