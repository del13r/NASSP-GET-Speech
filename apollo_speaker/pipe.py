"""Named-pipe GET reader (worker thread) with automatic reconnect, plus a simulator."""
from __future__ import annotations

import sys
import threading
import time

from PySide6.QtCore import QThread, Signal

from .getutil import format_get, parse_pipe_message, parse_ts, normalize_get
from .pause_protocol import PauseProtocolError, PauseRecordParser

DISCONNECTED = "disconnected"  # pipe missing / closed, retrying
WAITING = "waiting"  # pipe open but no data arriving
CONNECTED = "connected"  # data flowing


class PipeReader(QThread):
    sample = Signal(str, float, float)  # raw GET, time acceleration, time.monotonic() when read
    status = Signal(str, str)  # state, human readable detail

    RETRY_DELAY = 0.5
    STALE_SECONDS = 1.5
    RECYCLE_SECONDS = 3.0

    def __init__(self, pipe_name: str, parent=None):
        super().__init__(parent)
        self.pipe_name = pipe_name
        self._stop = threading.Event()
        self._last_status: tuple[str, str] | None = None

    def stop(self) -> None:
        self._stop.set()

    def _set_status(self, state: str, detail: str = "") -> None:
        if (state, detail) != self._last_status:
            self._last_status = (state, detail)
            self.status.emit(state, detail)

    def run(self) -> None:
        if sys.platform != "win32":
            self._set_status(DISCONNECTED, "Named pipes require Windows")
            return
        try:
            import pywintypes
            import winerror
            import win32event
            import win32file
            import win32pipe
        except ImportError as exc:
            self._set_status(DISCONNECTED, f"pywin32 is not installed ({exc})")
            return

        handle = None
        last_data = 0.0
        while not self._stop.is_set():
            if handle is None:
                handle = self._open(win32file, win32pipe, pywintypes)
                if handle is None:
                    self._stop.wait(self.RETRY_DELAY)
                    continue
                last_data = time.monotonic()
                self._set_status(WAITING, "Pipe open, waiting for GET data")

            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
            buf = win32file.AllocateReadBuffer(4096)
            try:
                try:
                    win32file.ReadFile(handle, buf, overlapped)
                except pywintypes.error as e:
                    if e.winerror != winerror.ERROR_IO_PENDING:
                        raise
                done = False
                while not self._stop.is_set():
                    rc = win32event.WaitForSingleObject(overlapped.hEvent, 100)
                    if rc != win32event.WAIT_TIMEOUT:
                        done = True
                        break
                    idle = time.monotonic() - last_data
                    if idle > self.STALE_SECONDS:
                        self._set_status(WAITING, "Pipe open but no GET data received")
                    if idle > self.RECYCLE_SECONDS:
                        break  # recycle a possibly dead connection (baseline reopened every read)
                if not done:
                    win32file.CancelIo(handle)
                    if self._stop.is_set():
                        break
                    handle.Close()
                    handle = None
                    continue
                n = win32file.GetOverlappedResult(handle, overlapped, True)
                if not n:
                    handle.Close()
                    handle = None
                    self._set_status(DISCONNECTED, "Pipe closed; reconnecting")
                    self._stop.wait(self.RETRY_DELAY)
                    continue
                msg = parse_pipe_message(bytes(buf[:n]))
                if msg is not None:
                    last_data = time.monotonic()
                    self._set_status(CONNECTED, "Receiving GET data")
                    self.sample.emit(msg[0], msg[1], last_data)
            except pywintypes.error as e:
                try:
                    handle.Close()
                except Exception:
                    pass
                handle = None
                self._set_status(DISCONNECTED, f"Pipe closed ({e.strerror.strip()}); reconnecting")
                self._stop.wait(self.RETRY_DELAY)
        if handle is not None:
            try:
                win32file.CancelIo(handle)
            except Exception:
                pass
            try:
                handle.Close()
            except Exception:
                pass

    def _open(self, win32file, win32pipe, pywintypes):
        try:
            handle = win32file.CreateFile(
                self.pipe_name,
                win32file.GENERIC_READ,
                0,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as e:
            if e.winerror == 2:
                self._set_status(DISCONNECTED, "Pipe not found - start Orbiter with the GET addon")
            elif e.winerror == 231:
                self._set_status(DISCONNECTED, "Pipe busy (another client connected?)")
            else:
                self._set_status(DISCONNECTED, f"Cannot open pipe: {e.strerror.strip()}")
            return None
        try:
            win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_BYTE, None, None)
        except pywintypes.error as e:
            if e.winerror != 5:
                handle.Close()
                self._set_status(DISCONNECTED, f"Cannot configure pipe: {e.strerror.strip()}")
                return None
        return handle


class SimulatedReader(QThread):
    """Stand-in for the pipe (``--simulate``): GET advances at ``accel`` from ``start``."""

    sample = Signal(str, float, float)
    status = Signal(str, str)

    def __init__(self, start_get: str = "+000:00:00", accel: float = 1.0, parent=None):
        super().__init__(parent)
        self.start_get = parse_ts(normalize_get(start_get))
        self.accel = accel
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.status.emit(CONNECTED, "Simulated GET source")
        t0 = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            get = self.start_get + (now - t0) * self.accel
            self.sample.emit(format_get(get), self.accel, now)
            self._stop.wait(0.1)


class PauseReader(PipeReader):
    """Read the independent pause stream; None means the state is unknown."""

    pause_changed = Signal(object, float)
    pause_seen = Signal(float)
    PIPE_NAME = r"\\.\pipe\GroundElapsedTimePauseV1"
    HEARTBEAT_TIMEOUT = 1.5

    def __init__(self, parent=None):
        super().__init__(self.PIPE_NAME, parent)

    def run(self) -> None:
        if sys.platform != "win32":
            self.pause_changed.emit(None, time.monotonic())
            return
        try:
            import pywintypes
            import winerror
            import win32event
            import win32file
            import win32pipe
        except ImportError:
            self.pause_changed.emit(None, time.monotonic())
            return

        while not self._stop.is_set():
            handle = self._open(win32file, win32pipe, pywintypes)
            if handle is None:
                self.pause_changed.emit(None, time.monotonic())
                self._stop.wait(self.RETRY_DELAY)
                continue
            parser = PauseRecordParser()
            last_data = time.monotonic()
            try:
                while not self._stop.is_set():
                    overlapped = pywintypes.OVERLAPPED()
                    overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
                    buf = win32file.AllocateReadBuffer(4096)
                    issued = False
                    completed = False
                    try:
                        try:
                            win32file.ReadFile(handle, buf, overlapped)
                            issued = True
                        except pywintypes.error as exc:
                            if exc.winerror != winerror.ERROR_IO_PENDING:
                                raise
                            issued = True
                        while not self._stop.is_set():
                            rc = win32event.WaitForSingleObject(overlapped.hEvent, 100)
                            if rc != win32event.WAIT_TIMEOUT:
                                break
                            if time.monotonic() - last_data > self.HEARTBEAT_TIMEOUT:
                                raise PauseProtocolError("Pause heartbeat timed out")
                        if self._stop.is_set():
                            break
                        completed = True
                        n = win32file.GetOverlappedResult(handle, overlapped, True)
                        if not n:
                            raise PauseProtocolError("Pause pipe closed")
                        last_data = time.monotonic()
                        previous_records = parser.records
                        for paused in parser.feed(bytes(buf[:n])):
                            self.pause_changed.emit(paused, last_data)
                        if parser.records != previous_records:
                            self.pause_seen.emit(last_data)
                    finally:
                        try:
                            if issued and not completed:
                                win32file.CancelIo(handle)
                                try:
                                    win32file.GetOverlappedResult(handle, overlapped, True)
                                except pywintypes.error as exc:
                                    if exc.winerror != winerror.ERROR_OPERATION_ABORTED:
                                        raise
                        finally:
                            overlapped.hEvent.Close()
            except (pywintypes.error, PauseProtocolError):
                self.pause_changed.emit(None, time.monotonic())
            finally:
                handle.Close()
            if not self._stop.is_set():
                self._stop.wait(self.RETRY_DELAY)
