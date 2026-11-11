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

- **Browser tabs** (with the extension): each tab that played sound gets its own row under
  its browser, with pause/play, mute and volume. Without it, Windows only sees one player
  and one sound stream for the whole of Chrome.

## Browser extension

Chrome, Edge and Brave: open `chrome://extensions`, turn on Developer mode, click
**Load unpacked** and pick the `extension` folder. After changing its code, click the
reload icon on the extension.

It works on its own, without the desktop app:

- **Toolbar popup**: Now playing and Recent tabs with play/pause, mute and volume;
  click a tab to go to it, ◉ pauses all the other tabs, ⏸ All pauses everything.
- **Badge** with the number of tabs making sound.
- **Only one tab plays at a time** (option): starting a tab pauses the others.
- **Shortcuts**: Alt+Shift+P plays/pauses the current or last paused tab; "Pause all"
  can be given a key at `chrome://extensions/shortcuts`.

When the desktop app is running it also sends the tabs there (`127.0.0.1:8765`) and
keeps retrying while it isn't; the popup has a switch to turn this off.

- Tab volume sets the level of the page's video/audio, so the site's own slider won't move.
- Tabs have no level meter; Chrome doesn't give per-tab levels.
- Only Audio Hub's own window and browser extensions can connect; websites are refused.

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
