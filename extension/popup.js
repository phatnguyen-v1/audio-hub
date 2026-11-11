// Popup mixer: asks the background worker for the tab list every second and sends it commands.

const $ = id => document.getElementById(id);
let dragging = null;

const send = msg => chrome.runtime.sendMessage(msg);
const cmd = (c, tabId, value) => send({ type: "cmd", cmd: c, tabId, value }).then(refresh);

function ago(ms) {
  if (!ms) return "";
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function makeRow(id) {
  const n = el(`<div class="row">
    <div class="top">
      <img class="icon" alt="">
      <div class="info" title="Go to this tab"><div class="name"></div><div class="sub"></div></div>
      <button class="pp"></button>
      <button class="solo" title="Pause the other tabs">◉</button>
      <button class="x" title="Remove from Recent">✕</button>
    </div>
    <div class="vol"><button class="mute"></button><input type="range" min="0" max="100"><span class="pct"></span></div>
  </div>`);
  n.querySelector(".info").onclick = () => cmd("focus", id).then(() => window.close());
  n.querySelector(".pp").onclick = () => cmd("playpause", id);
  n.querySelector(".solo").onclick = () => cmd("solo", id);
  n.querySelector(".x").onclick = () => cmd("dismiss", id);
  const range = n.querySelector("input");
  range.addEventListener("pointerdown", () => { dragging = range; });
  range.addEventListener("change", () => { if (dragging === range) dragging = null; });
  range.addEventListener("input", () => {
    n.querySelector(".pct").textContent = range.value + "%";
    send({ type: "cmd", cmd: "volume", tabId: id, value: range.value / 100 });
  });
  const mute = n.querySelector(".mute");
  mute.onclick = () => cmd("mute", id, !mute.classList.contains("on"));
  return n;
}

function updateRow(n, t, section, othersPlaying) {
  const icon = n.querySelector(".icon");
  if (t.favIconUrl && icon.getAttribute("src") !== t.favIconUrl) icon.src = t.favIconUrl;
  icon.style.visibility = t.favIconUrl ? "visible" : "hidden";
  n.querySelector(".name").textContent = t.title || t.site;
  n.querySelector(".name").title = t.title;
  const status = t.playing ? "" : (t.hasMedia ? "paused" : "stopped");
  n.querySelector(".sub").textContent =
    [t.site, status, section === "recent" ? ago(t.lastPlayed) : ""].filter(Boolean).join(" · ");

  const pp = n.querySelector(".pp");
  pp.hidden = !t.hasMedia;
  pp.textContent = t.playing ? "⏸" : "▶";
  pp.title = t.playing ? "Pause" : "Play";
  n.querySelector(".solo").hidden = !(t.playing && othersPlaying);
  n.querySelector(".x").hidden = section === "playing";

  const vol = n.querySelector(".vol");
  const range = vol.querySelector("input");
  vol.hidden = false;
  range.hidden = !t.hasMedia;
  vol.querySelector(".pct").hidden = !t.hasMedia;
  if (t.hasMedia && dragging !== range) {
    range.value = Math.round((t.volume ?? 1) * 100);
    vol.querySelector(".pct").textContent = range.value + "%";
  }
  const mute = vol.querySelector(".mute");
  mute.classList.toggle("on", t.muted);
  mute.textContent = t.muted ? "🔇" : "🔊";
  mute.title = t.muted ? "Unmute tab" : "Mute tab";
}

// Keyed update so rows aren't rebuilt while you click or drag.
function sync(container, tabs, section, othersPlaying) {
  const cache = container._nodes || (container._nodes = new Map());
  const seen = new Set();
  tabs.forEach((t, i) => {
    let n = cache.get(t.id);
    if (!n) { n = makeRow(t.id); cache.set(t.id, n); }
    updateRow(n, t, section, othersPlaying);
    if (container.children[i] !== n) container.insertBefore(n, container.children[i] || null);
    seen.add(t.id);
  });
  for (const [id, n] of cache) if (!seen.has(id)) { n.remove(); cache.delete(id); }
  $(section + "Empty").hidden = tabs.length > 0;
}

// Now playing keeps the order tabs appeared in, so rows don't jump under the mouse.
let playOrder = [];
function steady(playing) {
  const ids = playing.map(t => t.id);
  playOrder = playOrder.filter(id => ids.includes(id)).concat(ids.filter(id => !playOrder.includes(id)));
  return playOrder.map(id => playing.find(t => t.id === id));
}

async function refresh() {
  const s = await send({ type: "list" });
  if (!s) return;
  const playing = steady(s.playing);
  sync($("playing"), playing, "playing", playing.length > 1);
  sync($("recent"), s.recent, "recent", false);
  $("pauseAll").hidden = !playing.some(t => t.hasMedia);
  const hub = $("hub");
  hub.classList.toggle("on", s.hub);
  hub.textContent = !s.settings.hub ? "Desktop app off" : s.hub ? "Desktop app connected" : "Desktop app not running";
  $("onePlayer").checked = s.settings.onePlayer;
  $("hubSetting").checked = s.settings.hub;
}

$("pauseAll").onclick = () => cmd("pauseAll");
$("onePlayer").onchange = e => send({ type: "settings", value: { onePlayer: e.target.checked } }).then(refresh);
$("hubSetting").onchange = e => send({ type: "settings", value: { hub: e.target.checked } }).then(refresh);
$("shortcuts").onclick = e => { e.preventDefault(); chrome.tabs.create({ url: "chrome://extensions/shortcuts" }); };

refresh();
setInterval(refresh, 1000);
