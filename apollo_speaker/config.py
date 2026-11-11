"""Persistent application settings (JSON)."""
from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

PLAYBACK_TIMESTAMP = "timestamp"  # play at the trigger time; clips may overlap / PAO interrupts
PLAYBACK_SEQUENTIAL = "sequential"  # one clip at a time; later clips queue up

DEFAULT_VOICES = {
    "Conrad": {"model": "en_US-ryan-medium.onnx", "pan": "left"},
    "Gordon": {"model": "en_US-mike-medium.onnx", "pan": "left"},
    "Bean": {"model": "en_US-kusal-medium.onnx", "pan": "left"},
    "Carr": {"model": "en_US-mike-medium.onnx", "pan": "right"},
    "Gibson": {"model": "en_US-kusal-medium.onnx", "pan": "right"},
    "Public Affairs Officer": {"model": "en_US-hfc_male-medium.onnx", "pan": "right"},
    "Lind": {"model": "en_US-kusal-medium.onnx", "pan": "right"},
    "Weitz": {"model": "en_US-kusal-medium.onnx", "pan": "right"},
    "Stafford": {"model": "en_US-kusal-medium.onnx", "pan": "right"},
    "Schmitt": {"model": "en_US-kusal-medium.onnx", "pan": "right"},
}

PANS = ("left", "right", "center")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRANSCRIPT = "transcripts/apollo12_transcript.json"
LEGACY_DEFAULT_TRANSCRIPT = r"D:\python\apollo12_transcript_with_pao_get_human.json"


def resolve_transcript_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute() and p.parts and p.parts[0] == "transcripts":
        return PROJECT_ROOT / p
    return p


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "ApolloGetSpeaker"


def default_config_path() -> Path:
    env = os.environ.get("APOLLO_GET_SPEAKER_CONFIG")
    return Path(env) if env else config_dir() / "config.json"


def _default_voices() -> dict:
    return {k: dict(v) for k, v in DEFAULT_VOICES.items()}


@dataclass
class Config:
    transcript_file: str = DEFAULT_TRANSCRIPT
    model_dir: str = r"D:\piper\models"
    piper_python: str = "python"  # interpreter that has `piper` installed (python -m piper)
    wav_dir: str = ""  # empty = <config dir>/wav
    pipe_name: str = r"\\.\pipe\GroundElapsedTime"
    pregen_seconds: float = 3.0  # generate this many (real) seconds before an event
    late_grace_seconds: float = 4.0  # still speak an event that was ready this late
    max_speech_accel: float = 10.0  # non-PAO speech is skipped above this time acceleration
    settle_seconds: float = 1.5  # stay silent this long after start / a GET jump (ignores transient 000:00:00)
    speech_enabled: bool = True
    playback_policy: str = PLAYBACK_TIMESTAMP
    pao_override: bool = True  # a new PAO clip cuts off the previous PAO clip
    follow_current: bool = True
    site_sync_auto: bool = False  # unused
    site_mission: str = "13"  # unused
    site_offset_seconds: float = 0.0  # added to the GET sent to the browser extension
    window_geometry: str = ""
    voice_map: dict = field(default_factory=_default_voices)

    @property
    def resolved_wav_dir(self) -> str:
        return self.wav_dir or str(config_dir() / "wav")

    def voice_for(self, speaker: str) -> dict:
        v = self.voice_map.get(speaker)
        if v is None:
            v = self.voice_map.get("Lind") or DEFAULT_VOICES["Lind"]
        return {"model": v.get("model", ""), "pan": v.get("pan", "right")}

    def model_path(self, speaker: str) -> str:
        model = self.voice_for(speaker)["model"]
        return model if os.path.isabs(model) else os.path.join(self.model_dir, model)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        for key, value in data.items():
            if key == "voice_map" and isinstance(value, dict):
                merged = _default_voices()
                for sp, v in value.items():
                    if isinstance(v, dict):
                        merged.setdefault(sp, {}).update(v)
                cfg.voice_map = merged
            elif hasattr(cfg, key) and key != "voice_map":
                setattr(cfg, key, value)
        if cfg.playback_policy not in (PLAYBACK_TIMESTAMP, PLAYBACK_SEQUENTIAL):
            cfg.playback_policy = PLAYBACK_TIMESTAMP
        if ntpath.normcase(ntpath.normpath(cfg.transcript_file)) == ntpath.normcase(LEGACY_DEFAULT_TRANSCRIPT):
            cfg.transcript_file = DEFAULT_TRANSCRIPT
        return cfg

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        p = Path(path) if path else default_config_path()
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg = cls.from_dict(json.load(f))
        except (OSError, ValueError):
            cfg = cls()
        cfg._path = str(p)
        return cfg

    def save(self, path: str | os.PathLike | None = None) -> None:
        p = Path(path or getattr(self, "_path", None) or default_config_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, p)
