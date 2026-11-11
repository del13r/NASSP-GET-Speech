"""Background engine: owns the Scheduler and TTS generation off the UI thread."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot

from .scheduler import Clip, Job, Scheduler
from .tts import PiperGenerator, TtsError, wav_duration

TICK_MS = 10


class Engine(QObject):
    aligned = Signal(int)
    rewound = Signal(int)
    next_changed = Signal(int)  # index of next event, -1 when the transcript is finished
    fired = Signal(int)
    play_clip = Signal(object)
    stop_audio_requested = Signal()
    error = Signal(str)
    busy_changed = Signal(int)  # number of events being generated
    _generated = Signal(int, int, object, str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.generator = PiperGenerator(config)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="piper")
        self._scheduler: Scheduler | None = None
        self._timer: QTimer | None = None
        self._pending = 0
        self._pause_state: bool | None = None
        self._pause_event_t = 0.0
        self._last_sample_t = 0.0
        self._awaiting_running_sample = False
        self._generated.connect(self._on_generated)

    # ---- lifecycle ----
    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @Slot()
    def shutdown(self) -> None:
        if self._timer:
            self._timer.stop()
        self.generator.close()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- inputs ----
    @Slot(object)
    def set_timeline(self, timeline) -> None:
        self.generator.cancel_all()
        self._pending = 0
        self.busy_changed.emit(0)
        self._scheduler = Scheduler(timeline, self.config, self, self._submit)
        self._scheduler.clock.set_paused(
            self._pause_state is True or self._awaiting_running_sample, time.monotonic()
        )

    @Slot(str, float, float)
    def on_sample(self, raw_get: str, accel: float, t: float) -> None:
        if t < self._last_sample_t:
            return
        self._last_sample_t = t
        if self._scheduler:
            self._scheduler.on_sample(raw_get, accel, t)
        if self._pause_state is False and self._awaiting_running_sample and t >= self._pause_event_t:
            self._awaiting_running_sample = False
            if self._scheduler:
                self._scheduler.clock.set_paused(False, t)

    @Slot(object, float)
    def on_pause(self, paused: bool | None, t: float) -> None:
        if t < self._pause_event_t:
            return
        previous = self._pause_state
        self._pause_state = paused
        self._pause_event_t = t
        if paused is True:
            self._awaiting_running_sample = True
            freeze = True
        elif paused is None:
            self._awaiting_running_sample = False
            freeze = False
        elif previous is not False:
            self._awaiting_running_sample = self._last_sample_t < t
            freeze = self._awaiting_running_sample
        else:
            return
        if self._scheduler:
            self._scheduler.clock.set_paused(freeze, max(t, self._last_sample_t) if not freeze else t)

    @Slot(bool)
    def set_speech(self, enabled: bool) -> None:
        if self._scheduler:
            self._scheduler.set_speech(enabled)

    @Slot()
    def _tick(self) -> None:
        if self._scheduler:
            self._scheduler.tick(time.monotonic())

    # ---- generation ----
    def _submit(self, epoch: int, idx: int, jobs: list[Job]) -> None:
        self._pending += 1
        self.busy_changed.emit(self._pending)
        ev = self._scheduler.timeline[idx]
        try:
            self._pool.submit(self._work, epoch, ev, jobs)
        except RuntimeError:
            self._pending -= 1  # pool already shut down

    def _work(self, epoch: int, ev, jobs: list[Job]) -> None:
        clips: list[Clip] = []
        err = ""
        for job in jobs:
            try:
                path = self.generator.generate(ev.ts, ev.index, job.item, job.speaker, job.text)
            except TtsError as exc:
                err = str(exc)
                continue
            except Exception as exc:  # an unexpected failure must not kill the pool thread
                err = f"TTS error: {exc}"
                continue
            if path:
                clips.append(
                    Clip(ev.index, job.item, path, job.speaker, job.is_pao, job.is_null_pao, wav_duration(path))
                )
        try:
            self._generated.emit(epoch, ev.index, clips, err)
        except RuntimeError:
            pass  # engine deleted during shutdown

    @Slot(int, int, object, str)
    def _on_generated(self, epoch: int, idx: int, clips, err: str) -> None:
        self._pending = max(0, self._pending - 1)
        self.busy_changed.emit(self._pending)
        sched = self._scheduler
        if sched is None:
            return
        if err and epoch == sched.epoch and sched.speech_enabled:
            self.error.emit(err)
        sched.on_generated(epoch, idx, clips, time.monotonic())

    # ---- Sink protocol (called by Scheduler on the engine thread) ----
    def on_aligned(self, p: int) -> None:
        self.aligned.emit(p)

    def on_rewound(self, p: int) -> None:
        self.rewound.emit(p)

    def on_next(self, p: int) -> None:
        self.next_changed.emit(p)

    def on_fired(self, idx: int) -> None:
        self.fired.emit(idx)

    def play(self, clip: Clip) -> None:
        self.play_clip.emit(clip)

    def stop_audio(self) -> None:
        self.stop_audio_requested.emit()

    def cancel_generation(self) -> None:
        self.generator.cancel_all()


class EngineHost:
    """Owns the engine thread and its safe shutdown."""

    def __init__(self, config):
        self.thread = QThread()
        self.engine = Engine(config)
        self.engine.moveToThread(self.thread)
        self.thread.started.connect(self.engine.start)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.engine, "shutdown", Qt.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait(5000)
