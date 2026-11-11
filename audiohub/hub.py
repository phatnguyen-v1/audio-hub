"""Polls this machine's audio and serves the mixer page + a WebSocket.

The state is shaped as a list of machines so a Mac agent can join later.
"""
import asyncio
import json
import platform
import time
from pathlib import Path

import psutil
from aiohttp import WSMsgType, web

from .tracker import Tracker
from .win_audio import WinAudio, app_key, nice_name
from .win_media import WinMedia

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "extension://")

WEB = Path(__file__).parent / "web"
TICK = 0.2  # seconds; level meters update at this rate


class Hub:
    def __init__(self, recent_timeout=600):
        self.audio = WinAudio()
        self.media = WinMedia()
        self.tracker = Tracker(recent_timeout=recent_timeout)
        self.clients = set()
        self.ext = {}  # browser key ("chrome", "msedge", ...) -> {"ws": ..., "tabs": [...]}
        self.port = None
        self.machine = {"id": "local", "name": platform.node(), "os": "windows"}
        self._audio_state, self._media_state, self._running = {}, {}, set()

    # ---- polling ---------------------------------------------------------
    def _running_keys(self):
        keys = set()
        for p in psutil.process_iter(["name"]):
            if p.info["name"]:
                keys.add(app_key(p.info["name"]))
        return keys

    async def poll(self):
        n = 0
        while True:
            try:
                if n % 5 == 0:
                    self.audio.refresh_sessions()
                    self._media_state = await self.media.read()
                if n % 10 == 0:
                    self._running = await asyncio.to_thread(self._running_keys)
                self._audio_state = self.audio.read()
                now = time.monotonic()
                # With the extension connected, tabs replace the browser's single Windows player.
                media = {k: v for k, v in self._media_state.items()
                         if k.split("~")[0] not in self.ext}
                self.tracker.update(self._audio_state, media, self._running, now, self._tab_rows())
                if self.clients:
                    await self.broadcast(self.snapshot(now))
            except Exception as e:  # keep the loop alive; show the error in the console
                print("poll error:", repr(e))
            n += 1
            await asyncio.sleep(TICK)

    def _tab_rows(self):
        out = {}
        for browser, info in self.ext.items():
            for t in info["tabs"]:
                has_media = bool(t.get("hasMedia"))
                out[f"{browser}#{t['id']}"] = {
                    "parent": browser,
                    "parent_name": nice_name(browser),
                    "title": t.get("title") or "",
                    "site": t.get("site") or "",
                    # The page's own video/audio state is exact; Chrome's speaker flag lags
                    # a few seconds, so only use it for tabs without a media element.
                    "playing": (not t.get("paused")) if has_media else bool(t.get("audible")),
                    "has_media": has_media,
                    "volume": t.get("volume") if has_media else None,
                    "muted": bool(t.get("muted")),
                }
        return out

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        playing, recent = self.tracker.view(now)
        return {"machines": [{**self.machine, "master": self.audio.master(),
                              "playing": playing, "recent": recent}],
                "recentTimeout": self.tracker.recent_timeout}

    async def broadcast(self, state):
        msg = json.dumps(state)
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)

    # ---- commands from the page -------------------------------------------
    async def command(self, c):
        cmd, key = c.get("cmd"), c.get("key")
        if key and "#" in key:  # a browser tab: the extension does it
            if cmd == "dismiss":
                self.tracker.dismiss(key, time.monotonic())
                return
            browser, tab_id = key.split("#", 1)
            ext = self.ext.get(browser)
            if ext and cmd in ("playpause", "mute", "volume"):
                await ext["ws"].send_str(json.dumps(
                    {"cmd": cmd, "tabId": int(tab_id), "value": c.get("value")}))
                if cmd == "volume":
                    self.tracker.remember_volume(key, volume=float(c["value"]))
            return
        if cmd == "volume":
            self.audio.set_volume(key, c["value"])
            self.tracker.remember_volume(key, volume=float(c["value"]))
        elif cmd == "mute":
            self.audio.set_mute(key, c["value"])
            self.tracker.remember_volume(key, muted=bool(c["value"]))
        elif cmd == "master_volume":
            self.audio.set_master_volume(c["value"])
        elif cmd == "master_mute":
            self.audio.set_master_mute(c["value"])
        elif cmd in ("playpause", "next", "prev"):
            await self.media.command(key, cmd)
        elif cmd == "dismiss":
            self.tracker.dismiss(key, time.monotonic())

    # ---- web ---------------------------------------------------------------
    async def index(self, request):
        return web.FileResponse(WEB / "index.html")

    async def ws(self, request):
        # Only our own page may drive the mixer, not any website open in a browser.
        origin = request.headers.get("Origin", "")
        if origin and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_str(json.dumps(self.snapshot()))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self.command(json.loads(msg.data))
                    except Exception as e:
                        print("command error:", repr(e))
        finally:
            self.clients.discard(ws)
        return ws

    async def ext_ws(self, request):
        """The browser extension: reports its tabs, receives tab commands."""
        if not request.headers.get("Origin", "").startswith(EXTENSION_ORIGINS):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        browser = None
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("type") == "hello":
                    browser = str(data.get("browser") or "chrome").lower()
                    self.ext[browser] = {"ws": ws, "tabs": []}
                    print(f"extension connected: {browser}")
                elif data.get("type") == "tabs" and browser in self.ext:
                    self.ext[browser]["tabs"] = data.get("tabs") or []
        finally:
            if browser and self.ext.get(browser, {}).get("ws") is ws:
                del self.ext[browser]
                print(f"extension disconnected: {browser}")
        return ws

    async def run(self, host, port, ready=None):
        self.port = port
        await self.media.start()
        app = web.Application()
        app.add_routes([web.get("/", self.index), web.get("/ws", self.ws),
                        web.get("/ext", self.ext_ws)])
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        print(f"Audio Hub on http://{host}:{port}")
        if ready:
            ready()
        await self.poll()
