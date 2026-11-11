"""Windows per-app audio: list app audio sessions, read levels, set volume/mute.

Must be imported from the worker thread: comtypes initialises COM on import,
and we want a multithreaded apartment there (the UI thread needs its own STA).
"""
import os
import sys
import time

sys.coinit_flags = 0  # COINIT_MULTITHREADED, read by comtypes on import

from pycaw.pycaw import AudioUtilities, IAudioMeterInformation  # noqa: E402

EXPIRED = 2

NICE_NAMES = {
    "msedge": "Edge",
    "chrome": "Chrome",
    "firefox": "Firefox",
    "brave": "Brave",
    "vlc": "VLC",
    "mpc-hc64": "MPC-HC",
    "potplayermini64": "PotPlayer",
    "spotify": "Spotify",
    "discord": "Discord",
    "steamwebhelper": "Steam",
}


def app_key(exe_name: str) -> str:
    return os.path.splitext(exe_name)[0].lower()


def nice_name(key: str) -> str:
    return NICE_NAMES.get(key) or key[:1].upper() + key[1:]


class WinAudio:
    def __init__(self):
        self._sessions = {}  # key -> [(session, meter)]
        self._names = {}
        self._pending = {}  # key -> {"volume": float, "muted": bool} for apps with no stream yet
        self._device = None
        self._endpoint = None
        self._device_at = 0.0

    # ---- sessions -------------------------------------------------------
    def refresh_sessions(self):
        groups = {}
        for s in AudioUtilities.GetAllSessions():
            try:
                if not s.ProcessId or s.State == EXPIRED or s.Process is None:
                    continue
                key = app_key(s.Process.name())
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
            except Exception:
                continue  # process exited between listing and reading
            groups.setdefault(key, []).append((s, meter))
            if key not in self._names:
                dn = s.DisplayName or ""
                self._names[key] = dn if dn and not dn.startswith("@") else nice_name(key)
        self._sessions = groups
        for key in list(self._pending):
            if key in groups:
                p = self._pending.pop(key)
                if "volume" in p:
                    self.set_volume(key, p["volume"])
                if "muted" in p:
                    self.set_mute(key, p["muted"])

    def read(self):
        """key -> {name, peak, volume, muted} for apps that currently have a stream."""
        out = {}
        for key, lst in self._sessions.items():
            peak, vols, muted = 0.0, [], True
            for s, meter in lst:
                try:
                    peak = max(peak, meter.GetPeakValue())
                    sav = s.SimpleAudioVolume
                    vols.append(sav.GetMasterVolume())
                    muted = muted and bool(sav.GetMute())
                except Exception:
                    pass
            if vols:
                out[key] = {"name": self._names.get(key, nice_name(key)),
                            "peak": peak, "volume": max(vols), "muted": muted}
        return out

    def set_volume(self, key, value):
        value = min(1.0, max(0.0, float(value)))
        lst = self._sessions.get(key)
        if not lst:
            self._pending.setdefault(key, {})["volume"] = value
            return
        for s, _ in lst:
            try:
                s.SimpleAudioVolume.SetMasterVolume(value, None)
            except Exception:
                pass

    def set_mute(self, key, muted):
        lst = self._sessions.get(key)
        if not lst:
            self._pending.setdefault(key, {})["muted"] = bool(muted)
            return
        for s, _ in lst:
            try:
                s.SimpleAudioVolume.SetMute(1 if muted else 0, None)
            except Exception:
                pass

    # ---- output device --------------------------------------------------
    def _ensure_device(self):
        now = time.monotonic()
        if self._endpoint is None or now - self._device_at > 3:  # pick up default-device changes
            self._device = AudioUtilities.GetSpeakers()
            self._endpoint = self._device.EndpointVolume
            self._device_at = now

    def master(self):
        try:
            self._ensure_device()
            ev = self._endpoint
            return {"device": self._device.FriendlyName,
                    "volume": ev.GetMasterVolumeLevelScalar(),
                    "muted": bool(ev.GetMute())}
        except Exception:
            self._endpoint = None
            return {"device": "No output device", "volume": 0.0, "muted": True}

    def set_master_volume(self, value):
        self._ensure_device()
        self._endpoint.SetMasterVolumeLevelScalar(min(1.0, max(0.0, float(value))), None)

    def set_master_mute(self, muted):
        self._ensure_device()
        self._endpoint.SetMute(1 if muted else 0, None)
