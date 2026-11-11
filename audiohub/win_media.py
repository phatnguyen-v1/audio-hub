"""Windows media players (the ones in the Quick Settings media pop-up)."""
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as Manager,
)

from .win_audio import nice_name

PLAYING, PAUSED = 4, 5


def media_key(aumid: str) -> str:
    """Map an app id to the same key as its process name.

    'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' -> 'spotify'
    'Chrome' -> 'chrome', 'MSEdge' -> 'msedge', r'C:\\...\\vlc.exe' -> 'vlc'
    """
    part = aumid.split("!")[-1].replace("/", "\\").rsplit("\\", 1)[-1]
    if part.lower().endswith(".exe"):
        part = part[:-4]
    return part.lower()


class WinMedia:
    def __init__(self):
        self._mgr = None
        self._sessions = {}

    async def start(self):
        self._mgr = await Manager.request_async()

    async def read(self):
        """key -> {name, title, artist, status} for every registered player."""
        out, sessions = {}, {}
        for s in self._mgr.get_sessions():
            try:
                aumid = s.source_app_user_model_id
                key = media_key(aumid)
                st = int(s.get_playback_info().playback_status)
                status = "playing" if st == PLAYING else "paused" if st == PAUSED else "stopped"
                if key in out and out[key]["status"] == "playing":
                    continue  # several players from one app: keep the playing one
                try:
                    p = await s.try_get_media_properties_async()
                    title, artist = p.title or "", p.artist or ""
                except Exception:
                    title = artist = ""
            except Exception:
                continue
            out[key] = {"name": nice_name(key), "title": title, "artist": artist, "status": status}
            sessions[key] = s
        self._sessions = sessions
        return out

    async def command(self, key, cmd):
        s = self._sessions.get(key)
        if s is None:
            return
        if cmd == "playpause":
            await s.try_toggle_play_pause_async()
        elif cmd == "next":
            await s.try_skip_next_async()
        elif cmd == "prev":
            await s.try_skip_previous_async()
