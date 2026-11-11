import sys
import threading
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from apollo_speaker.pipe import DISCONNECTED, PauseReader, PipeReader


def test_reader_reconnects_after_gap_and_accepts_new_snapshot(monkeypatch):
    class WinError(Exception):
        winerror = 109

    class Event:
        def Close(self):
            pass

    class Handle:
        def __init__(self, chunks):
            self.chunks = iter(chunks)
            self.n = 0

        def Close(self):
            pass

    handles = iter([
        Handle([b"1 0 ", b"0\n1 1 1\n1 3 0\n"]),
        Handle([b"1 8 0\n"]),
    ])
    current = [None]

    def open_pipe(*args):
        current[0] = next(handles)
        return current[0]

    def read(handle, buf, _overlapped):
        chunk = next(handle.chunks)
        buf[:len(chunk)] = chunk
        handle.n = len(chunk)

    winfile = SimpleNamespace(
        AllocateReadBuffer=bytearray, ReadFile=read, CancelIo=lambda _: None,
        GetOverlappedResult=lambda handle, *_: handle.n, ERROR_IO_PENDING=997,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=WinError, OVERLAPPED=SimpleNamespace))
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace(
        CreateEvent=lambda *_: Event(), WaitForSingleObject=lambda *_: 0, WAIT_TIMEOUT=258,
    ))
    monkeypatch.setitem(sys.modules, "win32file", winfile)
    monkeypatch.setitem(sys.modules, "win32pipe", SimpleNamespace())
    reader = PauseReader()
    monkeypatch.setattr(reader, "_open", open_pipe)
    reader.RETRY_DELAY = 0
    states = []

    def on_pause(state, _timestamp):
        states.append(state)
        if states == [False, True, None, False]:
            reader.stop()

    reader.pause_changed.connect(on_pause)
    reader.run()
    assert states == [False, True, None, False]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes required")
def test_real_windows_byte_pipe():
    import pywintypes
    import win32file
    import win32pipe
    from PySide6.QtCore import Qt

    name = rf"\\.\pipe\ApolloSpeakerTest-{uuid.uuid4()}"
    pipe = win32pipe.CreateNamedPipe(
        name, win32pipe.PIPE_ACCESS_OUTBOUND,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT, 1, 4096, 4096, 0, None,
    )
    done = threading.Event()
    errors = []

    def serve():
        try:
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                    raise
            done.wait(0.15)  # exercise a genuinely pending overlapped read
            win32file.WriteFile(pipe, b"1 0 ")
            win32file.WriteFile(pipe, b"0\n1 0 0\n1 1 1\n1 2 0\n")
            done.wait(4)
        except pywintypes.error as exc:
            errors.append(exc)
        finally:
            pipe.Close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    reader = PauseReader()
    reader.pipe_name = name
    states = []
    seen = []

    def on_pause(state, _timestamp):
        states.append(state)
        if states[-3:] == [False, True, False]:
            done.set()
            reader.stop()

    reader.pause_changed.connect(on_pause, Qt.ConnectionType.DirectConnection)
    reader.pause_seen.connect(lambda t: seen.append(t), Qt.ConnectionType.DirectConnection)
    reader.start()
    try:
        assert done.wait(4), states
    finally:
        reader.stop()
        reader.wait(4000)
        done.set()
        server.join(4)
    assert not reader.isRunning()
    assert not errors
    assert states[-3:] == [False, True, False]
    assert seen


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named pipes required")
def test_legacy_pipe_disconnect_is_reported():
    import pywintypes
    import win32pipe
    from PySide6.QtCore import Qt

    name = rf"\\.\pipe\ApolloSpeakerTest-{uuid.uuid4()}"
    pipe = win32pipe.CreateNamedPipe(
        name, win32pipe.PIPE_ACCESS_OUTBOUND,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT, 1, 4096, 4096, 0, None,
    )
    closed = threading.Event()

    def serve():
        try:
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:
                    raise
        finally:
            pipe.Close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    reader = PipeReader(name)
    statuses = []

    def on_status(state, detail):
        statuses.append((state, detail))
        if state == DISCONNECTED:
            closed.set()
            reader.stop()

    reader.status.connect(on_status, Qt.ConnectionType.DirectConnection)
    reader.start()
    try:
        assert closed.wait(4), statuses
    finally:
        reader.stop()
        reader.wait(4000)
        server.join(4)
    assert not reader.isRunning()
