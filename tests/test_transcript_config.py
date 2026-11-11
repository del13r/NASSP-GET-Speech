import json

from apollo_speaker.config import (
    DEFAULT_TRANSCRIPT, LEGACY_DEFAULT_TRANSCRIPT, PLAYBACK_SEQUENTIAL,
    PLAYBACK_TIMESTAMP, PROJECT_ROOT, Config, resolve_transcript_path,
)
from apollo_speaker.transcript import FALLBACK, PAO, discover_transcripts, match_speaker, parse_events, resolve_speaker
from apollo_speaker.speakers import PORTRAIT_FILES, assets_dir


def test_speaker_resolution():
    assert resolve_speaker("Conrad (CDR)") == "Conrad"
    assert resolve_speaker("Public Affairs Officer") == PAO
    assert resolve_speaker("Mystery Voice") == FALLBACK
    assert match_speaker("Mystery Voice") is None
    assert resolve_speaker(None) == FALLBACK
    assert match_speaker("Schmitt") == "Schmitt"
    assert match_speaker("Stafford") == "Stafford"


def test_parse_events_sorts_and_attaches_null_get_entries():
    data = {
        "events": [
            {"GET": "+000:00:10", "speaker": "Carr", "text": "later"},
            {"GET": "+000:00:05", "speaker": "Public Affairs Officer", "text": "pao"},
            {"GET": None, "speaker": "Public Affairs Officer", "text": "attached"},
            {"GET": "+000:00:05", "speaker": "Mystery", "text": "same time"},
        ]
    }
    ev = parse_events(data)
    assert [e.ts for e in ev] == ["+000:00:05", "+000:00:05", "+000:00:10"]
    assert [e.index for e in ev] == [0, 1, 2]
    assert ev[0].is_pao and ev[0].pao[0].text == "attached" and ev[0].pao[0].is_null_pao
    assert ev[1].main.label == "Mystery" and ev[1].main.speaker == FALLBACK and not ev[1].main.known
    assert parse_events(data["events"])[0].sec == 5  # bare list also supported


def test_orphan_null_get_entry_is_ignored():
    assert parse_events([{"GET": None, "speaker": "Carr", "text": "x"}]) == []


def test_portraits_are_bundled():
    for name in PORTRAIT_FILES.values():
        assert (assets_dir() / "portraits" / name).is_file(), name


def test_config_roundtrip_and_voice_map(tmp_path):
    p = tmp_path / "cfg.json"
    cfg = Config.load(p)
    assert cfg.playback_policy == PLAYBACK_TIMESTAMP
    cfg.playback_policy = PLAYBACK_SEQUENTIAL
    cfg.voice_map["Conrad"] = {"model": "custom.onnx", "pan": "center"}
    cfg.model_dir = str(tmp_path)
    cfg.save()
    again = Config.load(p)
    assert again.playback_policy == PLAYBACK_SEQUENTIAL
    assert again.voice_for("Conrad") == {"model": "custom.onnx", "pan": "center"}
    assert again.model_path("Conrad") == str(tmp_path / "custom.onnx")
    assert again.voice_for("Nobody")["model"]  # falls back to Lind's voice


def test_config_tolerates_bad_files_and_partial_voice_maps(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text("{not json")
    assert Config.load(p).voice_map["Bean"]["model"]
    p.write_text(json.dumps({"voice_map": {"Bean": {"model": "x.onnx"}}, "playback_policy": "bogus"}))
    cfg = Config.load(p)
    assert cfg.voice_for("Bean") == {"model": "x.onnx", "pan": "left"}
    assert cfg.voice_for("Conrad")["model"] == "en_US-ryan-medium.onnx"
    assert cfg.playback_policy == PLAYBACK_TIMESTAMP


def test_default_and_legacy_transcript_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Config().transcript_file == DEFAULT_TRANSCRIPT
    assert resolve_transcript_path(Config().transcript_file) == PROJECT_ROOT / "transcripts" / "apollo12_transcript.json"
    assert resolve_transcript_path(DEFAULT_TRANSCRIPT).is_file()
    assert Config.from_dict({"transcript_file": LEGACY_DEFAULT_TRANSCRIPT}).transcript_file == DEFAULT_TRANSCRIPT
    assert Config.from_dict({"transcript_file": "custom.json"}).transcript_file == "custom.json"


def test_discover_transcripts(tmp_path):
    (tmp_path / "apollo12_transcript.json").touch()
    (tmp_path / "apollo_13_transcript.json").touch()
    (tmp_path / "notes.txt").touch()
    assert discover_transcripts(tmp_path) == [
        ("Apollo 12", "transcripts/apollo12_transcript.json"),
        ("Apollo 13", "transcripts/apollo_13_transcript.json"),
    ]
    assert discover_transcripts(tmp_path / "missing") == []
