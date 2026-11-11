from apollo_speaker.clock import GetClock
from apollo_speaker.config import Config
from apollo_speaker.scheduler import Clip, Scheduler
from apollo_speaker.transcript import parse_events
from apollo_speaker.tts import prepare_text


class FakeSink:
    def __init__(self):
        self.log = []
        self.clips = []

    def on_aligned(self, p):
        self.log.append(("aligned", p))

    def on_rewound(self, p):
        self.log.append(("rewound", p))

    def on_next(self, p):
        self.log.append(("next", p))

    def on_fired(self, i):
        self.log.append(("fired", i))

    def play(self, clip):
        self.clips.append(clip)
        self.log.append(("play", clip.event_index, clip.item))

    def stop_audio(self):
        self.log.append(("stop",))

    def cancel_generation(self):
        self.log.append(("cancel",))

    def of(self, kind):
        return [e for e in self.log if e[0] == kind]


def make(events, **cfg):
    config = Config()
    config.settle_seconds = 0.0
    for k, v in cfg.items():
        setattr(config, k, v)
    sink = FakeSink()
    jobs = []
    sched = Scheduler(
        parse_events(events), config, sink, lambda ep, idx, js: jobs.append((ep, idx, js)), GetClock(stale_after=1e9)
    )
    return sched, sink, jobs


def ev(ts, speaker, text="hi"):
    return {"GET": ts, "speaker": speaker, "text": text}


def finish(sched, jobs, now, idx=None):
    """Complete outstanding generation jobs with fake clips."""
    while jobs:
        ep, i, js = jobs.pop(0)
        clips = [Clip(i, j.item, f"{i}.wav", j.speaker, j.is_pao, j.is_null_pao, 1.0) for j in js]
        sched.on_generated(ep, i, clips, now)


EVENTS = [
    ev("+000:00:02", "Conrad"),
    ev("+000:00:05", "Carr"),
    ev("+000:00:20", "Public Affairs Officer"),
]


def test_alignment_skips_events_before_current_get():
    s, sink, jobs = make(EVENTS)
    s.on_sample("+000:00:03", 1.0, 0.0)
    assert s.pointer == 1
    assert sink.of("aligned") == [("aligned", 1)]
    s.tick(0.0)
    assert sink.of("fired") == []  # nothing due yet


def test_pregeneration_then_fire_plays_ready_clip():
    s, sink, jobs = make(EVENTS, pregen_seconds=3)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    assert [j[1] for j in jobs] == [0]  # only event 0 (2s away) is inside the 3 s window
    finish(s, jobs, 0.0)
    s.tick(2.05)
    assert sink.of("fired") == [("fired", 0)]
    assert sink.of("play") == [("play", 0, 0)]


def test_late_generation_plays_when_ready_within_grace():
    s, sink, jobs = make(EVENTS, pregen_seconds=3, late_grace_seconds=4)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    s.tick(2.5)  # fired but audio still being generated; scheduling continues
    assert sink.of("fired") == [("fired", 0)] and sink.of("play") == []
    s.clock.update(2, 1.0, 3.0)
    finish(s, jobs, 3.0)
    assert sink.of("play") == [("play", 0, 0)]


def test_too_late_generation_is_dropped():
    s, sink, jobs = make(EVENTS, late_grace_seconds=1)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    s.tick(2.5)
    s.clock.update(8, 1.0, 9.0)
    finish(s, jobs, 9.0)
    assert sink.of("play") == []
    assert ("fired", 0) in sink.log


def test_speech_off_still_fires_but_never_generates_or_plays():
    s, sink, jobs = make(EVENTS, speech_enabled=False)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    s.tick(2.1)
    assert jobs == [] and sink.of("play") == []
    assert sink.of("fired") == [("fired", 0)]


def test_toggle_speech_off_cancels_and_stops():
    s, sink, jobs = make(EVENTS)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    s.set_speech(False)
    assert sink.of("cancel") and sink.of("stop")
    finish(s, jobs, 1.0)  # stale epoch -> ignored
    s.tick(2.1)
    assert sink.of("play") == [] and sink.of("fired") == [("fired", 0)]
    s.set_speech(True)
    assert s.speech_enabled


def test_rewind_repositions_pointer_and_discards_stale_audio():
    s, sink, jobs = make(EVENTS)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    s.tick(2.1)
    s.tick(5.1)
    assert s.pointer == 2
    s.on_sample("+000:00:06", 1.0, 6.0)
    s.on_sample("+000:00:01", 1.0, 6.1)
    assert sink.of("rewound") == [("rewound", 0)]
    assert s.pointer == 0
    assert sink.of("stop") and sink.of("cancel")
    stale = list(jobs)
    jobs.clear()
    ep, i, js = stale[0]
    s.on_generated(ep, i, [Clip(i, 0, "x.wav", "Conrad", False, False)], 6.0)
    assert sink.of("play") == []
    s.tick(6.0)
    s.tick(7.2)  # event 0 fires again after the rewind
    assert sink.of("fired").count(("fired", 0)) == 2


def test_forward_jump_realigns_silently_and_drops_stale_audio():
    s, sink, jobs = make(EVENTS, late_grace_seconds=2)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    jobs.clear()
    s.on_sample("+000:10:00", 1.0, 1.0)
    assert s.pointer == 3 and sink.of("rewound") == [("rewound", 3)]
    assert sink.of("stop") and sink.of("cancel")
    s.tick(1.0)
    assert sink.of("play") == []


def test_load_save_after_transient_zero_then_speaks_at_new_get():
    events = [ev("+000:00:01", "Conrad", "early"), ev("+114:38:10", "Carr", "late")]
    s, sink, jobs = make(events, settle_seconds=1.5, pregen_seconds=3)
    s.on_sample("+000:00:00", 1.0, 0.0)  # transient launchpad zero
    s.tick(0.0)
    s.tick(1.2)
    assert jobs == [] and sink.of("play") == []  # nothing spoken while settling
    s.on_sample("+114:37:58", 1.0, 1.3)  # save loaded
    assert sink.of("rewound") == [("rewound", 1)]
    s.tick(1.4)
    assert jobs == []  # still holding
    s.on_sample("+114:38:00", 1.0, 3.0)
    s.tick(3.0)
    assert jobs == []  # event is still 10 s away, outside the pregen window
    s.tick(10.0)
    assert [j[1] for j in jobs] == [1]
    finish(s, jobs, 10.0)
    s.tick(13.5)
    assert sink.of("play") == [("play", 1, 0)]


def test_small_forward_sync_adjustment_does_not_reset_speech():
    s, sink, jobs = make(EVENTS)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.on_sample("+000:00:03", 1.0, 3.0)
    assert sink.of("rewound") == [] and sink.of("stop") == []


def test_backward_sync_adjustment_replays_from_new_position():
    s, sink, jobs = make(EVENTS)
    s.on_sample("+000:00:30", 1.0, 0.0)
    s.on_sample("+000:00:03", 1.0, 1.0)
    assert sink.of("rewound") == [("rewound", 1)] and s.pointer == 1


def test_crew_speech_skipped_at_high_accel_but_pao_kept():
    s, sink, jobs = make(EVENTS, max_speech_accel=10)
    s.on_sample("+000:00:00", 50.0, 0.0)
    s.tick(0.0)
    requested = {j[1]: [x.speaker for x in j[2]] for j in jobs}
    assert 0 not in requested  # crew event has nothing to generate at 50x
    s2, sink2, jobs2 = make([ev("+000:00:01", "Public Affairs Officer")], max_speech_accel=10)
    s2.on_sample("+000:00:00", 50.0, 0.0)
    s2.tick(0.0)
    assert len(jobs2) == 1


def test_same_timestamp_events_are_independent():
    events = [ev("+000:00:02", "Conrad", "a"), ev("+000:00:02", "Bean", "b")]
    s, sink, jobs = make(events)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    assert sorted(j[1] for j in jobs) == [0, 1]
    finish(s, jobs, 0.0)
    s.tick(2.1)
    assert sink.of("play") == [("play", 0, 0), ("play", 1, 0)]


def test_pao_and_attached_pao_jobs_keep_unmodified_display_text():
    main_text = "Roger [static] Houston."
    pao_text = "[cough] Go [aside] for launch."
    events = [
        {"GET": "+000:00:01", "speaker": "Conrad", "text": main_text},
        {"GET": None, "speaker": "Public Affairs Officer", "text": pao_text},
    ]
    s, sink, jobs = make(events)
    s.on_sample("+000:00:00", 1.0, 0.0)
    s.tick(0.0)
    generated = jobs[0][2]
    assert [job.text for job in generated] == [main_text, pao_text]
    assert generated[1].is_null_pao
    assert s.timeline[0].pao[0].text == pao_text
    assert prepare_text(generated[0].text) == "Roger Houston."
    assert prepare_text(generated[1].text) == "Go for launch."


def test_scheduler_attaches_current_acceleration_to_played_clip():
    s, sink, jobs = make([ev("+000:00:01", "Public Affairs Officer")])
    s.on_sample("+000:00:00", 20.0, 0.0)
    s.tick(0.1)
    finish(s, jobs, 0.1)
    assert len(sink.clips) == 1
    assert sink.clips[0].accel == 20.0
