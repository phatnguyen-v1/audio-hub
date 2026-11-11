"""Now Playing / Recent bookkeeping. Platform-neutral, so a Mac agent can reuse it.

Rules:
- Playing: the player says it's playing, or the app made sound in the last PLAY_HOLD s.
- Recent: stopped/paused things, newest play first.
  * still registered as a player (paused)  -> stays while the player exists, can resume
  * no player, app still running (e.g. closed browser tab) -> drops after `timeout`
- App closed (no process, no player, no stream) -> removed at once.
- Dismissed (x) -> hidden until it plays again.

Browser tabs (reported by the browser extension) are child rows of their browser:
they follow the same rules, are removed when the tab closes, and are listed right
under their browser when it's in the same section.
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
        self.play_started = None  # when the current play began; keeps Now playing in a steady order
        self.dismissed_at = None
        self.volume = None
        self.muted = False
        self.parent = None       # browser key, for tab rows
        self.parent_name = None
        self.site = ""


class Tracker:
    def __init__(self, recent_timeout=600, recent_cap=10):
        self.recent_timeout = recent_timeout
        self.recent_cap = recent_cap
        self.rows = {}

    def update(self, audio, media, running, now, tabs=None):
        """audio/media/tabs: key -> dict; running: set of app keys with a live process.

        tabs[key] = {parent, parent_name, title, site, playing, has_media, volume, muted}
        """
        tabs = tabs or {}
        for key in set(audio) | set(media) | set(tabs) | set(self.rows):
            r = self.rows.get(key)
            t = tabs.get(key)
            if t is not None:
                if r is None:
                    r = self.rows[key] = Row(key)
                self._update_tab(r, t, now)
                continue
            if r is not None and r.parent is not None:
                self.rows.pop(key)  # tab closed, or the extension went away
                continue
            a, m = audio.get(key), media.get(key)
            if a is None and m is None and key not in running:
                self.rows.pop(key, None)  # app closed
                continue
            if r is None:
                r = self.rows[key] = Row(key)
            r.audio, r.media = a, m
            if a:
                r.name, r.volume, r.muted = a["name"], a["volume"], a["muted"]
                if a["peak"] > SOUND_FLOOR and not a["muted"]:
                    r.last_sound = now
            elif m:
                r.name = m["name"]
            self._set_playing(r, bool((m and m["status"] == "playing")
                                      or (r.last_sound is not None and now - r.last_sound < PLAY_HOLD)), now)

    @staticmethod
    def _set_playing(r, playing, now):
        if playing and not r.playing:
            r.play_started = now
        r.playing = playing
        if playing:
            r.last_played = now

    def _update_tab(self, r, t, now):
        r.parent, r.parent_name = t["parent"], t["parent_name"]
        r.name = t["title"] or t["site"] or "Tab"
        r.site = t["site"]
        r.audio = None
        self._set_playing(r, bool(t["playing"]), now)
        r.media = ({"status": "playing" if r.playing else "paused", "title": "", "artist": ""}
                   if t["has_media"] else None)
        r.volume, r.muted = t["volume"], t["muted"]

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
        # Now playing keeps the order things started in, so rows don't jump under the mouse.
        by_start = lambda r: (r.play_started or 0, r.key)  # noqa: E731
        by_recent = lambda r: -(r.last_played or float("-inf"))  # noqa: E731
        return (self._section(playing, by_start, None, now),
                self._section(recent, by_recent, self.recent_cap, now))

    def _section(self, rows, order, cap, now):
        """Sort, then put each browser's tabs right under it when both are in this section."""
        parents_here = {r.key for r in rows if r.parent is None}
        tops = sorted((r for r in rows if r.parent is None or r.parent not in parents_here), key=order)
        if cap is not None:
            tops = tops[:cap]
        out = []
        for top in tops:
            out.append(self._row(top, now, orphan=top.parent is not None))
            if top.parent is None:
                kids = sorted((r for r in rows if r.parent == top.key), key=order)
                out.extend(self._row(k, now, orphan=False) for k in kids)
        return out

    @staticmethod
    def _row(r, now, orphan):
        m = r.media or {}
        status = "playing" if r.playing else m.get("status", "stopped")
        if status == "playing" and not r.playing:
            status = "paused"
        return {
            "key": r.key,
            "name": r.name,
            "status": status,
            "title": m.get("title", ""),
            "artist": m.get("artist", "") or r.site,
            "volume": r.volume,
            "muted": r.muted,
            "peak": r.audio["peak"] if r.audio else 0.0,
            "media": r.media is not None,
            "ago": None if r.last_played is None else now - r.last_played,
            "parent": r.parent,
            "parentName": r.parent_name if orphan else None,
        }
