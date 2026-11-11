"""Incremental parser for the Orbiter pause pipe."""
from __future__ import annotations

import re
from collections.abc import Iterator


_RECORD = re.compile(rb"1 ([0-9]+) ([01])")
_MAX_SEQUENCE = (1 << 64) - 1
_MAX_LINE = 24


class PauseProtocolError(ValueError):
    """The stream cannot be trusted; reconnect for a new snapshot."""


class PauseRecordParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._sequence: int | None = None
        self._paused: bool | None = None
        self.records = 0

    def feed(self, data: bytes) -> Iterator[bool]:
        self._buffer.extend(data)
        while True:
            end = self._buffer.find(b"\n")
            if end < 0:
                if len(self._buffer) > _MAX_LINE:
                    raise PauseProtocolError("Pause record too long")
                return
            line = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            match = _RECORD.fullmatch(line)
            if not match or len(line) > _MAX_LINE:
                raise PauseProtocolError("Malformed pause record")
            sequence = int(match.group(1))
            paused = match.group(2) == b"1"
            if sequence > _MAX_SEQUENCE:
                raise PauseProtocolError("Pause sequence out of range")
            if self._sequence is not None:
                if sequence == self._sequence and paused == self._paused:
                    self.records += 1
                    continue
                if sequence != self._sequence + 1 or paused == self._paused:
                    raise PauseProtocolError("Pause sequence gap or inconsistent state")
            self._sequence = sequence
            self._paused = paused
            self.records += 1
            yield paused
