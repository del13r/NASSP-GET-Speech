"""Event scheduling: pre-generation, firing, rewind handling. Pure Python (no Qt)."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .clock import GetClock
from .getutil import normalize_get, parse_ts
from .transcript import Event


@dataclass
class Clip:
    event_index: int
    item: int
    path: str
    speaker: str
    is_pao: bool
    is_null_pao: bool
    duration: float = 0.0
    accel: float = 1.0


@dataclass
class Job:
    item: int
    speaker: str
    text: str
    is_pao: bool
    is_null_pao: bool


class Sink(Protocol):
    def on_aligned(self, pointer: int) -> None: ...
    def on_rewound(self, pointer: int) -> None: ...
    def on_next(self, pointer: int) -> None: ...
    def on_fired(self, event_index: int) -> None: ...
    def play(self, clip: Clip) -> None: ...
    def stop_audio(self) -> None: ...
    def cancel_generation(self) -> None: ...


@dataclass
class _State:
    pending: bool = True
    clips: list[Clip] = field(default_factory=list)
    fired: bool = False
    played: bool = False


class Scheduler:
    def __init__(
        self,
        timeline: list[Event],
        config,
        sink: Sink,
        submit: Callable[[int, int, list[Job]], None],
        clock: GetClock | None = None,
    ):
        self.timeline = timeline
        self.config = config
        self.sink = sink
        self.submit = submit
        self.clock = clock or GetClock()
        self.speech_enabled = config.speech_enabled
        self.pointer = 0
        self.aligned = False
        self.epoch = 0
        self.hold_until = 0.0
        self._holding = False
        self.states: dict[int, _State] = {}
        self._secs = [e.sec for e in timeline]

        # --- NEW: PAO queue state ---
        self.pao_busy = False
        self.pao_queue: list[Clip] = []

        # --- NEW: non‑PAO busy flag ---
        self.non_pao_busy = False

        # --- NEW: currently active PAO (for interruption) ---
        self.active_pao_clip: Clip | None = None

    # ---- inputs -------------------------------------------------------
    def on_sample(self, raw_get: str, accel: float, now: float) -> None:
        raw_sec = parse_ts(normalize_get(raw_get))
        jumped = self.aligned and self.clock.forward_jump(raw_sec, accel, now)
        rewound = self.clock.update(raw_sec, accel, now)
        if not self.aligned:
            self.aligned = True
            self._hold(now)
            self._set_pointer(bisect_left(self._secs, raw_sec))
            self.sink.on_aligned(self.pointer)
        elif rewound or jumped:
            self._reset_speech()
            self._hold(now)
            self._set_pointer(bisect_left(self._secs, raw_sec))
            self.sink.on_rewound(self.pointer)

    def _hold(self, now: float) -> None:
        self.hold_until = now + max(0.0, getattr(self.config, "settle_seconds", 0.0))

    def set_speech(self, enabled: bool) -> None:
        if enabled == self.speech_enabled:
            return
        self.speech_enabled = enabled
        if not enabled:
            self._reset_speech()

    def _reset_speech(self) -> None:
        self.epoch += 1
        self.states.clear()
        self.sink.cancel_generation()
        self.sink.stop_audio()
        self.pao_busy = False
        self.non_pao_busy = False
        self.pao_queue.clear()
        self.active_pao_clip = None

    def _set_pointer(self, p: int) -> None:
        self.pointer = p
        self.sink.on_next(p if p < len(self.timeline) else -1)

    # ---- periodic work --------------------------------------------------
    def tick(self, now: float) -> None:
        if not self.aligned:
            return
        est = self.clock.estimate(now)
        if est is None:
            return
        accel = self.clock.accel
        n = len(self.timeline)
        self._holding = now < self.hold_until

        if self.speech_enabled and not self._holding:
            window = self.config.pregen_seconds * max(1.0, accel)
            i = self.pointer
            while i < n and self.timeline[i].sec - est <= window:
                if i not in self.states:
                    self._request(i, accel)
                i += 1

        moved = False
        while self.pointer < n and self.timeline[self.pointer].sec <= est:
            self._fire(self.pointer, est, accel)
            self.pointer += 1
            moved = True
        if moved:
            self.sink.on_next(self.pointer if self.pointer < n else -1)

    def _lateness(self, ev: Event, est: float, accel: float) -> float:
        return (est - ev.sec) / max(accel, 1e-3)

    def _request(self, idx: int, accel: float) -> None:
        ev = self.timeline[idx]

        if accel > 1.0:
            if all(not u.is_pao for u in ev.utterances):
                return

        jobs: list[Job] = []
        for k, u in enumerate(ev.utterances):
            if k == 0 and not u.is_pao and accel > self.config.max_speech_accel:
                continue
            jobs.append(Job(k, u.speaker, u.text, u.is_pao, u.is_null_pao))

        self.states[idx] = _State(pending=bool(jobs))
        if jobs:
            self.submit(self.epoch, idx, jobs)

    def _fire(self, idx: int, est: float, accel: float) -> None:
        ev = self.timeline[idx]
        self.sink.on_fired(idx)
        if not self.speech_enabled or self._holding:
            self.states.pop(idx, None)
            return
        if self._lateness(ev, est, accel) > self.config.late_grace_seconds:
            self.states.pop(idx, None)
            return
        if idx not in self.states:
            self._request(idx, accel)
        st = self.states[idx]
        st.fired = True
        if not st.pending:
            self._play(idx, st)

    def on_generated(self, epoch: int, idx: int, clips: list[Clip], now: float) -> None:
        if epoch != self.epoch:
            return
        st = self.states.get(idx)
        if st is None:
            return
        st.pending = False
        st.clips = clips
        if st.fired and not st.played and self.speech_enabled:
            est = self.clock.estimate(now) or 0.0
            if self._lateness(self.timeline[idx], est, self.clock.accel) <= self.config.late_grace_seconds:
                self._play(idx, st)
            else:
                self.states.pop(idx, None)

    # ---- NEW PAO + non‑PAO coordinated playback ------------------------
    def _play(self, idx: int, st: _State) -> None:
        st.played = True

        for clip in st.clips:
            clip.accel = self.clock.accel

            # ---------------- PAO LOGIC ----------------
            if clip.is_pao:

                # Rule 2: PAO interruption at timescale > 1×
                if self.clock.accel > 1.0 and self.active_pao_clip:
                    self.sink.stop_audio()
                    self.active_pao_clip = None
                    self.pao_busy = False

                # Rule 1: Null PAO waits for non‑PAO
                if clip.is_null_pao and self.non_pao_busy:
                    self.pao_queue.append(clip)
                    continue

                # Normal PAO queuing
                if self.pao_busy:
                    self.pao_queue.append(clip)
                    continue

                # Play PAO now
                self.pao_busy = True
                self.active_pao_clip = clip
                self.sink.play(clip)
                self._release_pao_after(clip.duration)

            # ---------------- NON‑PAO LOGIC ----------------
            else:
                self.non_pao_busy = True
                self.sink.play(clip)
                self._release_non_pao_after(clip.duration)

        self.states.pop(idx, None)

    # ---- non‑PAO release -----------------------------------------------
    def _release_non_pao_after(self, duration: float) -> None:
        import threading
        threading.Timer(duration, self._release_non_pao).start()

    def _release_non_pao(self) -> None:
        self.non_pao_busy = False

        # If null‑PAO was waiting, start it now
        if self.pao_queue and not self.pao_busy:
            next_clip = self.pao_queue.pop(0)
            self.pao_busy = True
            self.active_pao_clip = next_clip
            self.sink.play(next_clip)
            self._release_pao_after(next_clip.duration)

    # ---- PAO release ----------------------------------------------------
    def _release_pao_after(self, duration: float) -> None:
        import threading
        threading.Timer(duration, self._release_pao).start()

    def _release_pao(self) -> None:
        self.pao_busy = False
        self.active_pao_clip = None

        if self.pao_queue:
            next_clip = self.pao_queue.pop(0)
            self.pao_busy = True
            self.active_pao_clip = next_clip
            self.sink.play(next_clip)
            self._release_pao_after(next_clip.duration)
