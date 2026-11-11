from types import MethodType, SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from apollo_speaker.bridge import Bridge
from apollo_speaker.clock import GetClock
from apollo_speaker.engine import Engine
from apollo_speaker.main_window import MainWindow
from apollo_speaker.pipe import CONNECTED, DISCONNECTED


def make_consumer():
    consumer = SimpleNamespace(
        clock=GetClock(), bridge=Bridge(), config=SimpleNamespace(site_offset_seconds=3.0),
        model=SimpleNamespace(set_current=lambda _: None), _last_target=None,
        _last_sample_get=None, _last_sample_t=0.0, _pause_event_t=0.0,
        _get_invalidated_t=0.0, _pause_seen_t=0.0, simulate=None,
        _pause_state=None, _awaiting_running_sample=True, _site_running=False,
        _site_due=None, _resync_seq=0, _show_error=lambda *args, **kwargs: None,
        pause_changed=SimpleNamespace(emit=lambda *args: None),
        conn_state=CONNECTED,
        _refresh_pill=lambda: None,
    )
    for name in ("_on_sample", "_on_pause", "_on_pause_seen", "_on_status", "_publish_site", "_site_tick"):
        setattr(consumer, name, MethodType(getattr(MainWindow, name), consumer))
    return consumer


def test_pause_resume_unknown_and_ordering(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    c = make_consumer()
    c._on_sample("+000:01:40", 1.0, now[0])
    assert c.bridge.snapshot()["paused"] is None
    c._on_pause(False, now[0])
    assert c.bridge.snapshot()["running"]

    now[0] = 100.4
    c._on_pause(True, now[0])
    frozen = c.bridge.snapshot()
    assert frozen["paused"] is True and not frozen["running"] and frozen["offset"] == 3.0
    now[0] = 110.0
    c._publish_site(now[0])
    assert c.bridge.snapshot()["get"] == pytest.approx(frozen["get"])
    assert c.bridge.snapshot()["paused"] is True

    c._on_pause(False, now[0])
    assert c.bridge.snapshot()["paused"] is None
    c._on_sample("+000:01:40", 1.0, 100.3)  # delayed pre-pause read
    assert c.bridge.snapshot()["paused"] is None
    c._on_sample("+000:01:40", 1.0, now[0])
    resumed = c.bridge.snapshot()
    assert resumed["running"] and resumed["seq"] > frozen["seq"]
    now[0] += 0.3
    assert c.bridge.snapshot()["get"] > resumed["get"]
    c.conn_state = DISCONNECTED
    c._publish_site(now[0])
    assert c.bridge.snapshot()["paused"] is None
    c.conn_state = CONNECTED

    c._on_pause(None, now[0])
    assert c.bridge.snapshot()["paused"] is None
    c._on_pause(True, now[0])
    assert c.bridge.snapshot()["paused"] is True
    c._on_pause(False, now[0] + 0.01)
    assert c.bridge.snapshot()["paused"] is None
    now[0] += 0.02
    c._on_sample("+000:01:41", 2.0, now[0])
    state = c.bridge.snapshot()
    assert state["paused"] is False and not state["running"] and state["accel"] == 2.0


def test_sample_delivered_before_running_snapshot(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    c = make_consumer()
    c._on_pause(True, 9.0)
    c._on_sample("+000:00:10", 1.0, 10.0)
    c._on_pause(False, 9.5)
    assert c.bridge.snapshot()["running"]


def test_engine_clock_freezes_until_post_resume_sample():
    clock = GetClock()
    scheduler = SimpleNamespace(clock=clock, on_sample=lambda _raw, accel, t: clock.update(10, accel, t))
    engine = SimpleNamespace(
        _scheduler=scheduler, _pause_state=None, _pause_event_t=0.0,
        _last_sample_t=0.0, _awaiting_running_sample=False,
    )
    Engine.on_sample(engine, "+000:00:10", 1.0, 10.0)
    Engine.on_pause(engine, True, 10.3)
    frozen = clock.estimate(10.3)
    assert clock.estimate(20.0) == pytest.approx(frozen)
    Engine.on_pause(engine, False, 20.0)
    assert clock.estimate(20.0) == pytest.approx(frozen)
    Engine.on_sample(engine, "+000:00:10", 1.0, 20.1)
    assert clock.estimate(20.2) > frozen


def test_disconnect_rejects_delayed_events_until_both_streams_are_fresh(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    c = make_consumer()
    c._on_sample("+000:01:40", 1.0, 100.0)
    c._on_pause(False, 100.0)
    assert c.bridge.snapshot()["running"]

    now[0] = 101.0
    c._on_status(DISCONNECTED, "closed")
    c._on_pause(True, 100.9)  # queued pause from before the GET connection failed
    assert c.bridge.snapshot()["paused"] is None
    now[0] = 101.1
    c._on_status(CONNECTED, "connected")
    c._on_sample("+000:01:40", 1.0, 100.95)  # queued GET from the old connection
    assert c.bridge.snapshot()["paused"] is None
    c._on_pause_seen(101.1)  # confirmed heartbeat from pause stream
    assert c.bridge.snapshot()["paused"] is None
    c._on_sample("+000:01:40", 1.0, 101.1)
    assert c.bridge.snapshot()["paused"] is True

    now[0] = 102.0
    c._on_status(DISCONNECTED, "closed again")
    c._on_pause(None, 102.0)
    now[0] = 102.1
    c._on_status(CONNECTED, "connected")
    c._on_sample("+000:01:41", 1.0, 102.1)
    assert c.bridge.snapshot()["paused"] is None
    c._on_pause(False, 102.2)
    assert c.bridge.snapshot()["paused"] is None
    now[0] = 102.3
    c._on_sample("+000:01:41", 1.0, 102.3)
    assert c.bridge.snapshot()["running"]
