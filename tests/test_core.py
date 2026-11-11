import numpy as np

from apollo_speaker import theme
from apollo_speaker.clock import GetClock
from apollo_speaker.getutil import format_countdown, format_get, normalize_get, parse_pipe_message, parse_ts
from apollo_speaker.tts import pan_audio, prepare_text


def test_parse_and_normalize():
    assert normalize_get("+1:02:03") == "+001:02:03"
    assert parse_ts("-000:00:05") == -5
    assert parse_ts("+010:01:01") == 36061
    assert format_get(36061.9) == "+010:01:01"


def test_format_countdown():
    assert format_countdown(7.34) == "0:07.3"
    assert format_countdown(3725.0) == "1:02:05.0"
    assert format_countdown(-3) == "0:00.0"


def test_pipe_message_variants():
    assert parse_pipe_message(b"+012:34:56\n1.0\n") == ("+012:34:56", 1.0)
    assert parse_pipe_message(b"+012:34:56\r\n10") == ("+012:34:56", 10.0)
    assert parse_pipe_message(b"+000:00:01\n1\n+000:00:02\n1\n") == ("+000:00:02", 1.0)
    assert parse_pipe_message(b"garbage") is None
    assert parse_pipe_message(b"") is None


def test_clock_interpolates_between_samples():
    c = GetClock()
    c.update(100, 1.0, 0.0)
    assert c.estimate(0.0) == 100
    assert abs(c.estimate(0.5) - 100.5) < 1e-9
    # Never exceeds the next whole second while samples are fresh.
    c.update(100, 1.0, 1.2)
    assert c.estimate(1.2) < 101.0 + 0.3
    c.update(101, 1.0, 1.25)
    assert c.estimate(1.25) >= 101


def test_clock_is_monotonic_and_scales_with_accel():
    c = GetClock()
    c.update(0, 10.0, 0.0)
    last = c.estimate(0.0)
    for i in range(1, 50):
        t = i * 0.1
        c.update(int(t * 10), 10.0, t)
        est = c.estimate(t)
        assert est >= last
        last = est
    assert 48 <= last <= 51


def test_clock_ignores_small_backward_jitter():
    c = GetClock()
    c.update(100, 1.0, 0.0)
    assert c.update(99, 1.0, 0.5) is False
    assert c.rewind_count == 0
    assert c.update(50, 1.0, 1.0) is True


def test_clock_rewind_and_stale_freeze():
    c = GetClock(stale_after=1.0)
    c.update(500, 1.0, 0.0)
    assert c.update(100, 1.0, 1.0) is True
    assert c.rewind_count == 1
    assert abs(c.estimate(1.0) - 100) < 1e-9
    frozen = c.estimate(5.0)
    assert abs(frozen - 101.0) < 1e-6  # extrapolation stops after stale_after
    assert c.is_stale(5.0)


def test_countdown_colour_goes_red():
    far = theme.countdown_color(60)
    mid = theme.countdown_color(10)
    near = theme.countdown_color(0)
    assert near.red() > 240 and near.green() < 80
    assert far.green() > far.red()
    assert mid.red() > mid.blue()
    assert theme.countdown_color(3).green() < theme.countdown_color(8).green()


def test_prepare_text_baseline_rules():
    assert prepare_text("[garble]") == ""
    assert prepare_text("Okay - roger") == "Oh-kay, roger"
    out = prepare_text("We have 12,500 feet; go")
    assert "twelve thousand" in out and ";" not in out


def test_prepare_text_removes_bracketed_commentary_and_preserves_transcript():
    from apollo_speaker.transcript import parse_events

    text = "Roger [static] Houston, [crew\ncomment] standing by."
    event = parse_events([{"GET": "+000:00:01", "speaker": "Conrad", "text": text}])[0]
    assert event.main.text == text
    assert prepare_text(event.main.text) == "Roger Houston, standing by."
    assert prepare_text("Keep [an unmatched bracket") == "Keep [an unmatched bracket"


def test_pan_audio():
    a = np.array([1, 2, 3], dtype=np.int16)
    left = pan_audio(a, "left")
    right = pan_audio(a, "right")
    both = pan_audio(a, "center")
    assert left[:, 0].tolist() == [1, 2, 3] and left[:, 1].sum() == 0
    assert right[:, 1].tolist() == [1, 2, 3] and right[:, 0].sum() == 0
    assert both[:, 0].tolist() == both[:, 1].tolist() == [1, 2, 3]


def test_conrad_mumbling_regression_exact_text_and_path():
    import os
    from apollo_speaker.config import Config
    from apollo_speaker.tts import PiperGenerator, prepare_text

    cfg = Config()
    raw_text = "Now, if you'll just hold still a minute...No, you're going to have to turn around and get in the corner."
    prep = prepare_text(raw_text)

    # 1. Exact prepared text verification: ellipsis without spaces must separate sentences with period and space
    expected_prep = "Now, if you'll just hold still a minute. No, you're going to have to turn around and get in the corner."
    assert prep == expected_prep

    gen = PiperGenerator(cfg)

    # 2. Verify that the buggy unspaced text produced the exact hash and path of the attached mumbling WAV
    buggy_text = "Now, if you'll just hold still a minute.No, you're going to have to turn around and get in the corner."
    old_path = gen.cache_path("115:12:23", 11627, 0, "Conrad", buggy_text)
    assert old_path.endswith("115-12-23_11627_0_Conrad_d5da448f.wav")

    # 3. Verify that the fixed prepared text produces a distinct new cache path avoiding the mumbled file
    new_path = gen.cache_path("115:12:23", 11627, 0, "Conrad", prep)
    assert new_path.endswith("115-12-23_11627_0_Conrad_51445c35.wav")
    assert new_path != old_path

    # 4. If voice model is present, verify sentence segmentation splits into 2 sentences rather than 1 run-on
    try:
        from piper import PiperVoice

        model_path = cfg.model_path("Conrad")
        if os.path.isfile(model_path):
            voice = PiperVoice.load(model_path)
            sentences = voice.phonemize(prep)
            assert len(sentences) == 2, f"Expected 2 sentences, got {len(sentences)}"
    except ImportError:
        pass


def test_cache_wav_detection_and_filtering():
    from apollo_speaker.tts import is_cache_wav

    assert is_cache_wav("000-00-00_49_0_Conrad_3b350195.wav")
    assert is_cache_wav("115-12-23_11627_0_Conrad_d5da448f.wav")
    assert is_cache_wav("+000-00-01_1_0_PublicAffairsOfficer_797fe97d.wav")
    assert is_cache_wav("-000-00-05_0_0_Conrad_2efcd778.wav")
    assert is_cache_wav("test_0_0_Conrad_1234abcd.wav")
    assert is_cache_wav("001-57-56_596_0_Bean_981b87a0.wav.f8a20f.raw.wav")
    assert is_cache_wav("001-57-56_596_0_Bean_981b87a0.WAV")

    # Non-cache files
    assert not is_cache_wav("user_audio.wav")
    assert not is_cache_wav("transcript.json")
    assert not is_cache_wav("115-12-23_11627_0_Conrad.wav")
    assert not is_cache_wav("notes.txt")


def test_clear_wav_cache_deletes_only_cache_files_and_preserves_others(tmp_path):
    from apollo_speaker.tts import clear_wav_cache, scan_wav_cache

    cache_dir = tmp_path / "wav_cache"
    cache_dir.mkdir()

    # Create app cache files
    f1 = cache_dir / "000-00-00_49_0_Conrad_3b350195.wav"
    f2 = cache_dir / "115-12-23_11627_0_Conrad_d5da448f.wav"
    f3 = cache_dir / "001-57-56_596_0_Bean_981b87a0.wav.f8a20f.raw.wav"
    f1.write_bytes(b"wav1" * 100)
    f2.write_bytes(b"wav2" * 200)
    f3.write_bytes(b"raw3" * 50)

    # Create non-cache files
    user_file = cache_dir / "my_music.wav"
    keep_txt = cache_dir / "keep.txt"
    sub_dir = cache_dir / "subfolder"
    sub_dir.mkdir()
    sub_file = sub_dir / "000-00-00_49_0_Conrad_3b350195.wav"
    user_file.write_bytes(b"music")
    keep_txt.write_text("important", encoding="utf-8")
    sub_file.write_bytes(b"nested")

    total_count, total_bytes = scan_wav_cache(cache_dir)
    assert total_count == 3
    assert total_bytes == 400 + 800 + 200

    deleted, d_bytes, failed = clear_wav_cache(cache_dir)
    assert deleted == 3
    assert d_bytes == 1400
    assert failed == 0

    assert not f1.exists()
    assert not f2.exists()
    assert not f3.exists()

    # Preserved files
    assert user_file.exists()
    assert keep_txt.exists()
    assert sub_file.exists()


def test_clear_wav_cache_handles_locked_files_and_missing_dirs(tmp_path):
    from apollo_speaker.tts import clear_wav_cache, scan_wav_cache

    # Non-existent dir
    assert scan_wav_cache(tmp_path / "nonexistent") == (0, 0)
    assert clear_wav_cache(tmp_path / "nonexistent") == (0, 0, 0)

    cache_dir = tmp_path / "locked_cache"
    cache_dir.mkdir()

    f_open = cache_dir / "000-00-00_1_0_Conrad_11112222.wav"
    f_free = cache_dir / "000-00-00_2_0_Conrad_33334444.wav"
    f_open.write_bytes(b"open_data")
    f_free.write_bytes(b"free_data")

    # Hold f_open open to test robust error handling
    with open(f_open, "r+b"):
        deleted, d_bytes, failed = clear_wav_cache(cache_dir)
        # On Windows, opened file cannot be removed; on Unix it might be unlinked
        # In either case, the operation must not raise an unhandled exception and must delete free file
        assert not f_free.exists()
        assert deleted + failed == 2

    # After closing, remaining can be deleted
    if f_open.exists():
        deleted2, _, failed2 = clear_wav_cache(cache_dir)
        assert deleted2 == 1
        assert failed2 == 0
        assert not f_open.exists()


def test_format_size():
    from apollo_speaker.tts import format_size

    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024 * 5) == "5.0 MB"
    assert format_size(1024 * 1024 * 1024 * 2.5) == "2.5 GB"

