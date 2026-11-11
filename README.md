# Audio Hub

One mixer for the sound on all my machines. Windows only for now; the Mac agent comes later.

## What it shows

- **Master volume** of the current output device.
- **Now playing**: apps making sound or players that say they're playing, with track title,
  live level, volume, mute, and ⏮ ⏯ ⏭ for media players.
- **Recent**: paused or stopped apps, newest first.
  - A paused player still registered with Windows stays, and ▶ resumes it.
  - An app that stopped but is still running (e.g. a closed browser tab) drops after 10 minutes.
  - A closed app is removed at once. ✕ hides an item until it plays again.
  - Volume set on an app with no open stream is applied when it starts again.

## Run

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

Options (`python -m audiohub --help`): `--browser` opens it in the browser instead of a window,
`--headless` runs only the server, `--recent-minutes N` changes the Recent timeout, `--port`.

## How it works

`audiohub/hub.py` polls every 0.2 s and pushes state over a WebSocket to `web/index.html`.

- `win_audio.py`: per-app audio sessions (the Volume Mixer data) via pycaw.
- `win_media.py`: media players (the Quick Settings media pop-up) via WinRT.
- `tracker.py`: the Now playing / Recent rules; no Windows code, so the Mac agent can reuse it.

The state is a list of machines, so a Mac agent can join later by sending the same shape.
