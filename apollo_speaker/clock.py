"""Smooth GET estimation from the (1 s resolution) values received over the pipe."""
from __future__ import annotations

import threading


class GetClock:
    """Interpolates GET between pipe samples using the latest time acceleration.

    The pipe sends whole seconds (floor of the true GET), so the true value is in
    ``[sample, sample + 1 + age * accel)``. The estimate advances at ``accel`` from the
    moment the integer second last changed and is clamped to that window, which keeps it
    monotonic and accurate to about one pipe-update period.
    """

    REWIND_TOLERANCE = 2.0  # backward jitter smaller than this (GET seconds) is not a rewind

    def __init__(self, stale_after: float = 1.5):
        self._lock = threading.Lock()
        self.stale_after = stale_after
        self._sample_get: float | None = None
        self._sample_t = 0.0
        self._anchor_get = 0.0
        self._anchor_t = 0.0
        self._accel = 1.0
        self._paused = False
        self.rewind_count = 0

    @property
    def has_data(self) -> bool:
        return self._sample_get is not None

    @property
    def accel(self) -> float:
        return self._accel

    @staticmethod
    def jump_threshold(accel: float) -> float:
        return max(5.0, 2.0 * accel)

    def forward_jump(self, get_sec: float, accel: float, now: float) -> bool:
        """True if a sample is far ahead of where the running estimate says GET should be
        (loading a save, MFD sync advance, resuming after a long pause)."""
        est = self.estimate(now)
        return est is not None and get_sec - est > self.jump_threshold(accel)

    def update(self, get_sec: float, accel: float, now: float) -> bool:
        """Feed a sample. Returns True if the GET moved backwards (rewind)."""
        with self._lock:
            if self._sample_get is not None and now < self._sample_t:
                return False
            rewound = False
            if self._sample_get is None:
                self._anchor_get, self._anchor_t = get_sec, now
            elif get_sec < self._sample_get - self.REWIND_TOLERANCE:
                rewound = True
                self.rewind_count += 1
                self._anchor_get, self._anchor_t = get_sec, now
            else:
                if get_sec != self._sample_get:
                    prev_est = self._estimate_locked(now)
                    # Keep the running estimate unless the integer second says otherwise.
                    self._anchor_get, self._anchor_t = max(float(get_sec), min(prev_est, get_sec + 1.0 - 1e-3)), now
                if accel != self._accel:
                    self._anchor_get = self._estimate_locked(now, clamp=False)
                    self._anchor_t = now
            self._sample_get = float(get_sec)
            self._sample_t = now
            self._accel = accel
            return rewound

    def set_paused(self, paused: bool, now: float) -> None:
        with self._lock:
            if paused == self._paused:
                return
            if self._sample_get is not None:
                effective = min(now, self._sample_t + self.stale_after)
                self._anchor_get = self._estimate_locked(effective)
                self._anchor_t = now
            self._paused = paused

    def _estimate_locked(self, now: float, clamp: bool = True) -> float:
        est = self._anchor_get + (0.0 if self._paused else max(0.0, now - self._anchor_t)) * self._accel
        if not clamp or self._sample_get is None:
            return est
        age = max(0.0, now - self._sample_t)
        lo = self._sample_get
        hi = self._sample_get + 1.0 + age * self._accel
        return min(max(est, lo), hi)

    def is_stale(self, now: float) -> bool:
        return self._sample_get is None or (now - self._sample_t) > self.stale_after

    def estimate(self, now: float) -> float | None:
        """Estimated GET in seconds, or None if no sample has ever arrived.

        When samples stop arriving (paused sim / pipe gone) extrapolation is limited to
        ``stale_after`` seconds and then freezes.
        """
        with self._lock:
            if self._sample_get is None:
                return None
            eff_now = min(now, self._sample_t + self.stale_after)
            return self._estimate_locked(eff_now)

    def reset(self) -> None:
        with self._lock:
            self._sample_get = None
