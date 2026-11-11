import json
import threading
import time

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from apollo_speaker import theme
from apollo_speaker.chat import ChatModel, RowRole
from apollo_speaker.config import Config
from apollo_speaker.main_window import MainWindow, UpNextCard
from apollo_speaker.transcript import parse_events

SAMPLE = [
    {"get": "+000:00:02", "speaker": "Conrad", "text": "Hello there."},
    {"get": "+000:00:04", "speaker": "Houston", "text": "Roger."},
]


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(theme.STYLESHEET)
    return app


def make_config(tmp_path, transcript):
    cfg = Config()
    cfg._path = str(tmp_path / "config.json")
    cfg.transcript_file = str(transcript)
    cfg.model_dir = str(tmp_path / "models")
    cfg.wav_dir = str(tmp_path / "wav")
    cfg.speech_enabled = False
    return cfg


def test_window_simulated_run_and_clean_close(qapp, tmp_path):
    t = tmp_path / "t.json"
    t.write_text(json.dumps(SAMPLE), encoding="utf-8")
    win = MainWindow(make_config(tmp_path, t), simulate=("+000:00:00", 1.0))
    win.show()
    pump(qapp, 2.0)
    win.close()
    pump(qapp, 0.2)


def test_window_missing_transcript_does_not_crash(qapp, tmp_path):
    win = MainWindow(make_config(tmp_path, tmp_path / "missing.json"), simulate=("+000:00:00", 1.0))
    win.show()
    pump(qapp, 0.8)
    win.close()
    pump(qapp, 0.2)


def test_selector_switches_during_load_and_persists(qapp, tmp_path, monkeypatch):
    from apollo_speaker import main_window

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(SAMPLE), encoding="utf-8")
    second.write_text(json.dumps([{"get": "+000:00:03", "speaker": "Bean", "text": "Second mission"}]), encoding="utf-8")
    monkeypatch.setattr(main_window, "discover_transcripts", lambda: [("First", str(first)), ("Second", str(second))])
    real_load = main_window.load_transcript
    started, release = threading.Event(), threading.Event()

    def delayed_load(path):
        if path == str(first):
            started.set()
            assert release.wait(5)
        return real_load(path)

    monkeypatch.setattr(main_window, "load_transcript", delayed_load)
    cfg = make_config(tmp_path, first)
    win = MainWindow(cfg, simulate=("+000:00:00", 1.0))
    try:
        assert win.windowTitle() == "NASSP GET Speech"
        win._show_view(0)
        assert started.wait(2)
        win.transcript_selector.setCurrentIndex(1)
        release.set()
        pump(qapp, 1)
        assert [event.main.text for event in win.timeline] == ["Second mission"]
        assert Config.load(cfg._path).transcript_file == str(second)
    finally:
        release.set()
        win.close()
        pump(qapp, 0.2)


def test_selector_keeps_custom_path_and_reports_missing(qapp, tmp_path):
    missing = tmp_path / "custom.json"
    win = MainWindow(make_config(tmp_path, missing), simulate=("+000:00:00", 1.0))
    try:
        assert win.transcript_selector.currentData() == str(missing)
        win._show_view(0)
        pump(qapp, 0.5)
        assert "Transcript not found" in win.banner.text()
    finally:
        win.close()
        pump(qapp, 0.2)


def test_bundled_default_loads_in_transcript_view(qapp, tmp_path):
    cfg = make_config(tmp_path, Config().transcript_file)
    win = MainWindow(cfg, simulate=("+000:00:00", 1.0))
    try:
        assert win.transcript_selector.currentText() == "Apollo 12"
        win._show_view(0)
        deadline = time.monotonic() + 10
        while not win.timeline and time.monotonic() < deadline:
            pump(qapp, 0.05)
        assert len(win.timeline) > 10000
    finally:
        win.close()
        pump(qapp, 0.2)


def test_bracketed_commentary_remains_in_chat_and_up_next(qapp, tmp_path):
    text = "Roger [static] Houston, [crew asleep] standing by."
    event = parse_events([{"GET": "+000:00:02", "speaker": "Conrad", "text": text}])[0]
    model = ChatModel(make_config(tmp_path, tmp_path / "unused.json"))
    model.set_timeline([event])
    assert model.data(model.index(0)) == f"{event.ts} Conrad: {text}"

    card = UpNextCard(big=True)
    card.set_event(event)
    assert card.text.text() == text


def test_follow_scrolls_to_distant_event(qapp, tmp_path):
    events = [
        {"get": "+%03d:%02d:%02d" % (i // 3600, i // 60 % 60, i % 60), "speaker": "Conrad", "text": "Line %d of the long transcript." % i}
        for i in range(0, 40000 * 10, 10)
    ]
    t = tmp_path / "big.json"
    t.write_text(json.dumps(events), encoding="utf-8")
    win = MainWindow(make_config(tmp_path, t), simulate=("+110:00:00", 1.0))
    win.resize(900, 700)
    win.show()
    pump(qapp, 3.0)
    idx = win.model.next_idx
    assert idx > 30000
    index = win.model.index(win.model.row_for_event(idx))
    assert win.view.visualRect(index).intersects(win.view.viewport().rect())
    win.close()
    pump(qapp, 0.2)


def test_settings_dialog_clear_cache_interaction(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from apollo_speaker.dialogs import SettingsDialog

    wav_dir = tmp_path / "test_wavs"
    wav_dir.mkdir()
    f1 = wav_dir / "000-00-00_1_0_Conrad_12345678.wav"
    f1.write_bytes(b"wavdata")

    cfg = Config()
    cfg.wav_dir = str(wav_dir)

    class DummyParent(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.stopped = False
            self.player = self

        def stop_all(self):
            self.stopped = True

    parent = DummyParent()
    dlg = SettingsDialog(cfg, parent)

    # 1. Test clicking Clear cache and declining confirmation
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dlg.clear_cache_btn.click()
    assert f1.exists()
    assert not parent.stopped

    # 2. Test clicking Clear cache and accepting confirmation
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    info_shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: info_shown.append(args))
    dlg.clear_cache_btn.click()

    assert not f1.exists()
    assert parent.stopped
    assert len(info_shown) == 1
    assert "Successfully deleted 1 cached WAV file(s)" in info_shown[0][2]

    # 3. Test clicking Clear cache when cache is now empty
    empty_info = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: empty_info.append(args))
    dlg.clear_cache_btn.click()
    assert len(empty_info) == 1
    assert "No cached WAV files found" in empty_info[0][2]
    dlg.close()

