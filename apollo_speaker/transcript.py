"""Transcript loading. Parsing/alignment rules follow the original GET script."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECT_ROOT
from .getutil import parse_ts

PAO = "Public Affairs Officer"
FALLBACK = "Lind"
ASTRONAUTS = frozenset({"Conrad", "Gordon", "Bean"})

# Order matters: first match wins (same order as the original script).
_SPEAKER_KEYS = [
    ("conrad", "Conrad"),
    ("gordon", "Gordon"),
    ("bean", "Bean"),
    ("carr", "Carr"),
    ("gibson", "Gibson"),
    ("public affairs", PAO),
    ("pao", PAO),
    ("lind", "Lind"),
    ("weitz", "Weitz"),
    ("stafford", "Stafford"),
    ("schmitt", "Schmitt"),
    ("scmitt", "Schmitt"),
]

KNOWN_SPEAKERS = [
    "Conrad", "Gordon", "Bean", "Carr", "Gibson", PAO, "Lind", "Weitz", "Stafford", "Schmitt",
]


def discover_transcripts(directory: Path | None = None) -> list[tuple[str, str]]:
    directory = directory if directory is not None else PROJECT_ROOT / "transcripts"
    result = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name.casefold()):
        name = re.sub(r"(?i)(?:[_ -]+transcript)$", "", path.stem)
        name = re.sub(r"(?i)\b(apollo)[_ -]*(\d+)", r"\1 \2", name)
        name = re.sub(r"[_-]+", " ", name).strip().title()
        result.append((name or path.stem, f"transcripts/{path.name}"))
    return result


def match_speaker(raw: str | None) -> str | None:
    """Canonical speaker for a transcript label, or None when unrecognised."""
    if raw is None:
        return None
    s = raw.lower()
    for key, name in _SPEAKER_KEYS:
        if key in s:
            return name
    return None


def resolve_speaker(raw: str | None) -> str:
    """Voice key for a label: unknown speakers use the fallback voice (baseline rule)."""
    return match_speaker(raw) or FALLBACK


@dataclass
class Utterance:
    label: str  # speaker label exactly as in the transcript
    speaker: str  # voice key (canonical name, or fallback)
    known: bool  # label matched a known speaker (portrait / colour)
    text: str
    is_pao: bool
    is_null_pao: bool = False  # attached to an earlier event (transcript entry without GET)


@dataclass
class Event:
    index: int
    ts: str
    sec: int
    main: Utterance
    pao: list[Utterance] = field(default_factory=list)

    @property
    def utterances(self) -> list[Utterance]:
        return [self.main, *self.pao]

    @property
    def is_pao(self) -> bool:
        return self.main.is_pao


def _make_utterance(e: dict, null_pao: bool = False) -> Utterance:
    raw = e.get("speaker")
    speaker = resolve_speaker(raw)
    known = match_speaker(raw) is not None
    label = (raw or "").strip() or "Unknown"
    return Utterance(
        label=label,
        speaker=speaker,
        known=known,
        text=e.get("text", ""),
        is_pao=(speaker == PAO),
        is_null_pao=null_pao,
    )


def parse_events(data) -> list[Event]:
    events = data["events"] if isinstance(data, dict) and "events" in data else data
    items: list[tuple[int, str, Utterance]] = []
    last: list | None = None
    for e in events:
        ts = e.get("GET") or e.get("get")
        if ts is None:
            if last is not None:
                last[2].pao.append(_make_utterance(e, null_pao=True))
            continue
        utt = _make_utterance(e)
        last = [parse_ts(ts), ts, Event(0, ts, parse_ts(ts), utt)]
        items.append(last)
    # Stable sort by seconds (matches list.sort on the original timeline).
    items.sort(key=lambda it: it[0])
    out = [it[2] for it in items]
    for i, ev in enumerate(out):
        ev.index = i
    return out


def load_transcript(path: str) -> list[Event]:
    with open(path, "r", encoding="utf-8") as f:
        return parse_events(json.load(f))
