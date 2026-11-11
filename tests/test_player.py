import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from apollo_speaker.audio import Player
from apollo_speaker.config import PLAYBACK_SEQUENTIAL, PLAYBACK_TIMESTAMP
from apollo_speaker.scheduler import Clip


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


class FakeBackend:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start(self, path):
        self.started.append(path)
        backend = self

        class H:
            def stop(self_inner):
                backend.stopped.append(path)

        return H()


class FailingBackend:
    def start(self, path):
        raise OSError("audio device unavailable")


def clip(i, pao=False, dur=0.05, null=False, accel=1.0):
    return Clip(i, 0, f"{i}.wav", "X", pao, null, dur, accel)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_timestamp_policy_overlaps(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_TIMESTAMP)
    p.play(clip(1, dur=0.5))
    p.play(clip(2, dur=0.5))
    assert b.started == ["1.wav", "2.wav"] and p.active_count == 2
    p.stop_all()
    assert p.active_count == 0 and len(b.stopped) == 2


def test_backend_playback_failure_is_reported(app):
    p = Player(FailingBackend(), PLAYBACK_TIMESTAMP)
    errors = []
    p.playback_error.connect(errors.append)
    p.play(clip(1))
    assert p.active_count == 0
    assert errors == ["Could not play 1.wav: audio device unavailable"]


def test_pao_does_not_interrupt_in_timestamp_mode_at_or_below_10x(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_TIMESTAMP, pao_override=True)
    p.play(clip(1, dur=0.5))
    p.play(clip(2, pao=True, dur=0.5, accel=10.0))
    p.play(clip(3, pao=True, dur=0.5, accel=10.0))
    assert b.stopped == [] and p.active_count == 3
    p.stop_all()


def test_pao_interrupts_previous_pao_only_above_10x(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_TIMESTAMP, pao_override=True)
    p.play(clip(1, dur=0.5, accel=20.0))
    p.play(clip(2, pao=True, dur=0.5, accel=20.0))
    p.play(clip(3, pao=True, dur=0.5, accel=20.0))
    assert b.stopped == ["2.wav"]
    assert p.active_count == 2
    p.set_pao_override(False)
    p.play(clip(4, pao=True, dur=0.5, accel=20.0))
    assert p.active_count == 3
    p.stop_all()


class CompletionBackend:
    """Handles report completion themselves (like QSoundEffect) instead of relying on duration."""

    def __init__(self):
        self.handles = []

    def start(self, path):
        class H:
            done = False

            def stop(self):
                pass

        h = H()
        self.handles.append(h)
        return h


def test_clip_is_not_cut_off_before_backend_reports_completion(app):
    b = CompletionBackend()
    p = Player(b, PLAYBACK_TIMESTAMP)
    p.play(clip(1, dur=0.05))
    wait(400)  # well past duration + pad, e.g. a slow-loading effect
    assert p.active_count == 1
    b.handles[0].done = True
    wait(100)
    assert p.active_count == 0


def test_sequential_queue_advances_on_backend_completion(app):
    b = CompletionBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL)
    p.play(clip(1, dur=0.05))
    p.play(clip(2, dur=0.05))
    wait(300)
    assert len(b.handles) == 1 and p.queued_count == 1
    b.handles[0].done = True
    wait(100)
    assert len(b.handles) == 2 and p.queued_count == 0
    p.stop_all()


@pytest.mark.parametrize("accel", [1.0, 10.0])
def test_sequential_pao_interrupt_is_disabled_at_or_below_10x(app, accel):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL, pao_override=True)
    p.play(clip(1, pao=True, dur=1.0))
    p.play(clip(2, pao=True, dur=1.0, accel=accel))
    assert b.started == ["1.wav"]
    assert b.stopped == []
    assert p.queued_count == 1
    p.stop_all()


def test_sequential_pao_interrupt_is_enabled_above_10x(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL, pao_override=True)
    p.play(clip(1, pao=True, dur=1.0))
    p.play(clip(2, pao=True, dur=1.0, accel=10.01))
    assert b.started == ["1.wav", "2.wav"]
    assert b.stopped == ["1.wav"]
    assert p.active_count == 1 and p.queued_count == 0
    p.stop_all()


def test_sequential_high_accel_pao_respects_disabled_override(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL, pao_override=False)
    p.play(clip(1, pao=True, dur=1.0))
    p.play(clip(2, pao=True, dur=1.0, accel=20.0))
    assert b.started == ["1.wav"]
    assert b.stopped == []
    assert p.queued_count == 1
    p.stop_all()


def test_timestamp_policy_keeps_overlap_when_pao_override_disabled(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_TIMESTAMP, pao_override=False)
    p.play(clip(1, pao=True, dur=1.0))
    p.play(clip(2, pao=True, dur=1.0, accel=1.0))
    assert b.started == ["1.wav", "2.wav"]
    assert b.stopped == []
    assert p.active_count == 2
    p.stop_all()


def test_sequential_policy_queues_and_advances(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL)
    p.play(clip(1, dur=0.05))
    p.play(clip(2, dur=0.05))
    p.play(clip(3, dur=0.05))
    assert b.started == ["1.wav"] and p.queued_count == 2
    wait(600)
    assert b.started == ["1.wav", "2.wav", "3.wav"]
    assert p.active_count == 0 and p.queued_count == 0


def test_null_pao_queues_sequentially_in_timestamp_mode(app):
    b = CompletionBackend()
    p = Player(b, PLAYBACK_TIMESTAMP)
    p.play(clip(1, dur=0.05))
    p.play(clip(2, pao=True, null=True, dur=0.05))
    p.play(clip(3, pao=True, null=True, dur=0.05))
    assert len(b.handles) == 2
    assert p.queued_count == 1
    b.handles[1].done = True
    wait(100)
    assert len(b.handles) == 3
    assert p.queued_count == 0
    p.stop_all()


def test_stop_all_clears_queue(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL)
    p.play(clip(1, dur=1))
    p.play(clip(2, dur=1))
    p.stop_all()
    wait(100)
    assert b.started == ["1.wav"] and p.queued_count == 0


def test_switching_to_timestamp_releases_queue(app):
    b = FakeBackend()
    p = Player(b, PLAYBACK_SEQUENTIAL)
    p.play(clip(1, dur=1))
    p.play(clip(2, dur=1))
    p.set_policy(PLAYBACK_TIMESTAMP)
    assert b.started == ["1.wav", "2.wav"]
    p.stop_all()
