"""Now Playing / Recent bookkeeping. Platform-neutral, so a Mac agent can reuse it.

Rules:
- Playing: the player says it's playing, or the app made sound in the last PLAY_HOLD s.
- Recent: stopped/paused things, newest play first.
  * still registered as a player (paused)  -> stays while the player exists, can resume
  * no player, app still running (e.g. closed browser tab) -> drops after `timeout`
- App closed (no process, no player, no stream) -> removed at once.
- Dismissed (x) -> hidden until it plays again.
"""

PLAY_HOLD = 2.0     # seconds of silence before something counts as stopped (gaps between songs)
SOUND_FLOOR = 0.003  # peak level treated as silence


class Row:
    def __init__(self, key):
        self.key = key
        self.name = key
        self.audio = None
        self.media = None
        self.playing = False
        self.last_sound = None
        self.last_played = None
        self.dismissed_at = None
        self.volume = None
        self.muted = False


class Tracker:
    def __init__(self, recent_timeout=600, recent_cap=10):
        self.recent_timeout = recent_timeout
        self.recent_cap = recent_cap
        self.rows = {}

    def update(self, audio, media, running, now):
        for key in set(audio) | set(media) | set(self.rows):
            a, m = audio.get(key), media.get(key)
            if a is None and m is None and key not in running:
                self.rows.pop(key, None)  # app closed
                continue
            r = self.rows.get(key)
            if r is None:
                r = self.rows[key] = Row(key)
            r.audio, r.media = a, m
            if a:
                r.name, r.volume, r.muted = a["name"], a["volume"], a["muted"]
                if a["peak"] > SOUND_FLOOR and not a["muted"]:
                    r.last_sound = now
            elif m:
                r.name = m["name"]
            r.playing = bool((m and m["status"] == "playing")
                             or (r.last_sound is not None and now - r.last_sound < PLAY_HOLD))
            if r.playing:
                r.last_played = now

    def dismiss(self, key, now):
        r = self.rows.get(key)
        if r:
            r.dismissed_at = now

    def remember_volume(self, key, volume=None, muted=None):
        r = self.rows.get(key)
        if r:
            if volume is not None:
                r.volume = volume
            if muted is not None:
                r.muted = muted

    def view(self, now):
        playing, recent = [], []
        for r in self.rows.values():
            if r.playing:
                playing.append(r)
                continue
            if r.dismissed_at is not None and (r.last_played or 0) <= r.dismissed_at:
                continue
            if r.media is not None:
                recent.append(r)
            elif r.last_played is not None and now - r.last_played < self.recent_timeout:
                recent.append(r)
        playing.sort(key=lambda r: r.name.lower())
        recent.sort(key=lambda r: -(r.last_played or float("-inf")))
        return ([self._row(r, now) for r in playing],
                [self._row(r, now) for r in recent[: self.recent_cap]])

    @staticmethod
    def _row(r, now):
        m = r.media or {}
        status = "playing" if r.playing else m.get("status", "stopped")
        if status == "playing" and not r.playing:
            status = "paused"
        return {
            "key": r.key,
            "name": r.name,
            "status": status,
            "title": m.get("title", ""),
            "artist": m.get("artist", ""),
            "volume": r.volume,
            "muted": r.muted,
            "peak": r.audio["peak"] if r.audio else 0.0,
            "media": r.media is not None,
            "ago": None if r.last_played is None else now - r.last_played,
        }
