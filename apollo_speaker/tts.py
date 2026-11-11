"""Piper text preparation, WAV generation and stereo panning (baseline behaviour)."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import uuid
import wave

import numpy as np

_inflect = None


def _engine():
    global _inflect
    if _inflect is None:
        import inflect
        _inflect = inflect.engine()
    return _inflect


# NEW: Normalize smart quotes → ASCII
def normalize_quotes(text: str) -> str:
    return (
        text.replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
    )


def fix_hyphens(text: str) -> str:
    text = re.sub(r"\s*-\s*", ", ", text)
    text = text.replace("–", ", ")
    text = text.replace("—", ", ")
    text = text.replace("--", ", ")
    return text


def _convert_large_number(match) -> str:
    num = int(match.group(0).replace(",", ""))
    return _engine().number_to_words(
        num,
        andword="and",
        zero="zero"
    )

def segment_for_piper(text: str) -> str:
    # 1. Hyphens → full stop (always)
    text = re.sub(r"\s*[-–—]\s*", ". ", text)

    # 2. If the sentence is long, break commas into full stops
    if len(text) > 180:  # tune threshold if needed
        text = text.replace(",", ". ")
    else:
        # Keep commas for natural pauses
        text = text.replace(",", ", ")

    # 3. Break long conjunction chains
    if len(text) > 200:
        text = re.sub(r"\b(and|but|so|that|which)\b", r". \1", text)

    # 4. Clean up multiple periods
    text = re.sub(r"\.{2,}", ".", text)

    return text.strip()


def prepare_text(text: str) -> str:
    """Text sent to Piper; empty string means there is nothing to speak."""
    tts = re.sub(r"\s+", " ", re.sub(r"\[[\s\S]*?\]", " ", text)).strip()
    if not tts:
        return ""

    # NEW: normalize smart quotes BEFORE any other processing
    tts = normalize_quotes(tts)

    # NEW: segment long sentences for Piper
    tts = segment_for_piper(tts)

    tts = fix_hyphens(tts)
    for src in ("Okay", "OK", "Ok", "okay"):
        tts = tts.replace(src, "Oh-kay")
    tts = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", _convert_large_number, tts)
    tts = re.sub(r"\s*\.{2,}\s*", ". ", tts)
    tts = tts.replace(" .", ".")
    tts = tts.replace(" - ", " ").replace(";", ",")
    tts = re.sub(r"^\.\s*", "", tts)
    tts = re.sub(r"\s+", " ", tts).strip()
    return tts



def pan_audio(audio: np.ndarray, pan: str) -> np.ndarray:
    stereo = np.zeros((len(audio), 2), dtype=np.int16)
    if pan == "left":
        stereo[:, 0] = audio
    elif pan == "right":
        stereo[:, 1] = audio
    else:
        stereo[:, 0] = audio
        stereo[:, 1] = audio
    return stereo


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


class TtsError(Exception):
    pass


class PiperGenerator:
    """Runs Piper in subprocesses. Safe to call from worker threads; `cancel_all` kills them."""

    def __init__(self, config):
        self.config = config
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._closed = False

    def cache_path(self, event_ts: str, event_index: int, item: int, speaker: str, text: str) -> str:
        voice = self.config.voice_for(speaker)
        key = f"{text}|{voice['model']}|{voice['pan']}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        name = f"{event_ts.replace(':', '-')}_{event_index}_{item}_{speaker.replace(' ', '')}_{digest}.wav"
        return os.path.join(self.config.resolved_wav_dir, name)

    def generate(self, event_ts: str, event_index: int, item: int, speaker: str, text: str) -> str | None:
        """Return path to a stereo WAV, None if there is nothing to say, raise TtsError on failure."""
        piper_text = prepare_text(text)
        if not piper_text:
            return None
        outfile = self.cache_path(event_ts, event_index, item, speaker, piper_text)
        if os.path.exists(outfile):
            return outfile
        model = self.config.model_path(speaker)
        if not os.path.isfile(model):
            raise TtsError(f"Voice model not found: {model}")
        os.makedirs(os.path.dirname(outfile), exist_ok=True)
        raw = outfile + f".{uuid.uuid4().hex[:6]}.raw.wav"
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with self._lock:
                if self._closed:
                    raise TtsError("shutting down")
                proc = subprocess.Popen(
                    [self.config.piper_python, "-m", "piper", "-m", model, "-f", raw],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    creationflags=creation,
                )
                self._procs.add(proc)
        except FileNotFoundError:
            raise TtsError(f"Cannot run '{self.config.piper_python}'. Set the Piper Python in Settings.")
        try:
            try:
                _, err = proc.communicate(piper_text, timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise TtsError("Piper timed out")
        finally:
            with self._lock:
                self._procs.discard(proc)
        if proc.returncode != 0:
            last = (err or "").strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
            raise TtsError(f"Piper failed: {last[0]}")
        try:
            with wave.open(raw, "rb") as w:
                params = w.getparams()
                frames = w.readframes(params.nframes)
            audio = np.frombuffer(frames, dtype=np.int16)
            stereo = pan_audio(audio, self.config.voice_for(speaker)["pan"])
            with wave.open(outfile, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(params.framerate)
                w.writeframes(stereo.tobytes())
        except Exception as exc:
            raise TtsError(f"Bad Piper output: {exc}")
        finally:
            try:
                os.remove(raw)
            except OSError:
                pass
        return outfile

    def cancel_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.cancel_all()


CACHE_WAV_PATTERN = re.compile(
    r"^[\w+-]+_\d+_\d+_[A-Za-z0-9_-]+_[0-9a-fA-F]{8}\.wav(\.[0-9a-fA-F]+\.raw\.wav)?$",
    re.IGNORECASE,
)


def is_cache_wav(filename: str) -> bool:
    """Return True if filename matches Apollo Speaker generated WAV cache files."""
    return bool(CACHE_WAV_PATTERN.match(filename))


def scan_wav_cache(wav_dir: str | os.PathLike) -> tuple[int, int]:
    """Scan wav_dir and return (count, total_bytes) of app-generated cache WAV files."""
    if not os.path.isdir(wav_dir):
        return (0, 0)
    count = 0
    total_bytes = 0
    try:
        with os.scandir(wav_dir) as it:
            for entry in it:
                try:
                    if entry.is_file() and is_cache_wav(entry.name):
                        count += 1
                        total_bytes += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        return (0, 0)
    return (count, total_bytes)


def clear_wav_cache(wav_dir: str | os.PathLike) -> tuple[int, int, int]:
    """Delete app-generated WAV cache files in wav_dir.

    Safely targets only files matching the app's cache naming pattern.
    Handles open or locked files by catching OSError and continuing.

    Returns:
        (deleted_count, deleted_bytes, failed_count)
    """
    if not os.path.isdir(wav_dir):
        return (0, 0, 0)

    deleted_count = 0
    deleted_bytes = 0
    failed_count = 0

    try:
        with os.scandir(wav_dir) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                    if not is_cache_wav(entry.name):
                        continue
                    size = entry.stat().st_size
                    os.remove(entry.path)
                    deleted_count += 1
                    deleted_bytes += size
                except OSError:
                    failed_count += 1
    except OSError:
        pass

    return (deleted_count, deleted_bytes, failed_count)


def format_size(bytes_count: int | float) -> str:
    """Format byte counts into human-readable strings."""
    size = float(bytes_count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


__all__ = [
    "PiperGenerator",
    "TtsError",
    "prepare_text",
    "pan_audio",
    "wav_duration",
    "CACHE_WAV_PATTERN",
    "is_cache_wav",
    "scan_wav_cache",
    "clear_wav_cache",
    "format_size",
]
