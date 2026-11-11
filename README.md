# NASSP GET Speech

Windows PySide6 GUI that reads an Apollo transcript JSON, follows Ground Elapsed Time (GET)
sent over a Windows named pipe by your Orbiter add-on, and speaks each event with Piper TTS
at the right moment. Replaces the console script `GET.NP.10.py`.

## Setup

```
python -m pip install -r requirements.txt
```

Requires Windows, Python 3.10+, a working Piper install (`python -m piper`) and `.onnx` voice models.

## Run

```
python -m apollo_speaker
python -m apollo_speaker --simulate +000:00:00 --accel 1      # no Orbiter needed
python -m apollo_speaker --transcript C:\path\t.json --model-dir C:\piper\models
```

The default transcript is `transcripts\apollo12_transcript.json`. Open **Transcript** and
choose a mission from the selector. To add another mission, put its JSON file in the
project's `transcripts` folder; it appears when you next open the transcript view.
Files use the same event format as the bundled transcript. **Settings** and `--transcript`
also support a custom JSON path outside that folder.

First launch: open **Settings** and set the Piper models folder, the Python
that has Piper installed, and the pipe name. Config is saved to
`%APPDATA%\ApolloGetSpeaker\config.json` (override with `--config` or `APOLLO_GET_SPEAKER_CONFIG`).
The existing settings location is retained for compatibility. The old default transcript
path is replaced by the bundled Apollo 12 transcript; other saved custom paths are retained.
The default model directory is `D:\piper\models`; missing files show an error banner.

## Transcript provenance

The Apollo 12 JSON includes source and page references to the
[Apollo Flight Journal](https://apollojournals.org/afj/ap12fj/). Those references are
preserved in the data. Inclusion here does not imply an official endorsement or license.

## Features

- Smooth countdown (updates every ~16 ms), interpolated between the 1 s pipe samples and scaled
  by time acceleration. It keeps running while speech is being pre-generated. Colour ramps green → amber → red as it reaches 0.
- Speech toggle (F9 / button): off stops and cancels generation/playback, but events are still
  highlighted when they trigger.
- Playback policy: **Timestamp** (play at the event time, clips may overlap) or **Sequential** (one speaker
  at a time, later clips queue). A new PAO clip always cuts off a playing PAO clip above 10x time
  acceleration; at 10x or lower the selected policy alone decides.
- Chat-style transcript with portraits and per-speaker colours; "Up next" preview shows the
  next 5 events before they are spoken; Follow auto-scrolls.
- Jump resilience: forward jumps (loading a save) and backward/forward MFD sync adjustments realign
  silently and drop stale audio; a short `settle_seconds` hold after first sample ignores the
  transient 000:00:00 at startup.
- **Voices** dialog: per-speaker Piper model and pan (left/centre/right), with test button; persisted.
- GET rewind detection (replays from the new position, drops stale audio), named-pipe auto-reconnect,
  connection/runtime error states in the header and status bar, clean shutdown of all threads and Piper processes.
- Baseline behaviours kept: event parsing/ordering, null-GET PAO attachment, text clean-up, WAV panning,
  skipping main speech at high acceleration, PAO handling. Unlike the baseline, events with identical
  timestamps are handled independently, and generated WAVs are cached by content.

## Portraits

`assets/portraits/`: conrad, gordon, bean, gibson, carr, lind, Weitz, stafford, scmitt (`.png`),
mapped to the matching speakers; other speakers get a coloured initials avatar.

## Pipe protocol

Pipe message is `GET\naccel` (e.g. `+000:01:23\n1`). Pipe name is configurable.
The current add-on also publishes pause snapshots and transitions on
`\\.\pipe\GroundElapsedTimePauseV1`. Site sync requires this pause stream and a
current GET: if either is unavailable, the browser holds playback and reports
that sync is unavailable instead of guessing. Older add-ons still provide GET
and transcript functionality but cannot drive site sync until updated.

## Tests

```
python -m pytest -q
```

Covers GET parsing, clock, transcript/config, scheduler (fake sink), player policies and a headless window smoke test.
