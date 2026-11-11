import os
import subprocess
from pathlib import Path

import pytest


CHROME = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe"


def run_browser_test(tmp_path, setup, assertions, markup=None):
    script = (Path(__file__).parent.parent / "browser_extension" / "content.js").read_text(encoding="utf-8")
    if markup is None:
        markup = """
<button id="playPauseBtn" aria-label="Play"></button><input id="missionElapsedTime">
<button id="GETBtn"></button><pre id="result">NOT RUN</pre>
"""
    html = markup + """
<script>
const timers = [];
let nextTimer = 0;
window.setTimeout = (fn, delay) => { const id = ++nextTimer; timers.push({fn, delay, id}); return id; };
window.clearTimeout = id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers.splice(i, 1); };
document.body.classList.add("app-ready");
""" + setup + """
const button = document.getElementById("playPauseBtn");
let clicks = 0, seeks = 0;
button.onclick = () => {
  clicks++;
  button.setAttribute("aria-label", button.getAttribute("aria-label") === "Play" ? "Pause" : "Play");
};
document.getElementById("GETBtn").onclick = () => { seeks++; };
function runTimer(delay) {
  const index = timers.findIndex(t => t.delay === delay);
  if (index < 0) throw new Error("Missing timer " + delay);
  timers.splice(index, 1)[0].fn();
}
</script>
<script>
""" + script + """
</script>
<script>
try {
""" + assertions + """
  document.getElementById("result").textContent = "PASS";
} catch (e) {
  document.getElementById("result").textContent = "FAIL: " + e;
}
</script>"""
    page = tmp_path / "test.html"
    page.write_text(html, encoding="utf-8")
    result = subprocess.run(
        [str(CHROME), "--headless", "--disable-gpu", "--no-first-run",
         f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", page.as_uri()],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '<pre id="result">PASS</pre>' in result.stdout, result.stdout


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_browser_cancels_pending_seek_on_pause_and_unknown(tmp_path):
    run_browser_test(tmp_path, """
const states = [
  {get: 100, accel: 1, running: true, paused: false, seq: 1},
  {get: 100, accel: 1, running: false, paused: true, seq: 1},
  {get: 101, accel: 1, running: true, paused: false, seq: 2},
  {get: 101, accel: 1, running: false, paused: null, seq: 2}
];
let nextState = 0;
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({ok: true, state: states[nextState++]})}};
""", """
  runTimer(250); // paused while the first seek is pending
  runTimer(500);
  if (seeks !== 0 || button.getAttribute("aria-label") !== "Play") throw new Error("pause did not cancel seek");
  runTimer(250); // resume
  runTimer(500);
  if (seeks !== 1) throw new Error("resume did not seek");
  runTimer(250); // unknown state
  if (button.getAttribute("aria-label") !== "Play") throw new Error("unknown did not hold playback");
  if (!document.body.textContent.includes("unavailable")) throw new Error("unknown status missing");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_browser_does_not_interrupt_playing_but_retries_paused(tmp_path):
    run_browser_test(tmp_path, """
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({
  ok: true, state: {get: 100, accel: 1, running: true, paused: false, seq: 1}
})}};
""", """
  if (clicks !== 1) throw new Error("play-first did not start playback");
  runTimer(500);
  if (seeks !== 1) throw new Error("seek did not occur");
  for (let i = 0; i < 6; i++) runTimer(400);
  if (clicks !== 1 || button.getAttribute("aria-label") !== "Pause")
    throw new Error("successful playback was interrupted");
  button.setAttribute("aria-label", "Play");
  runTimer(400);
  if (clicks !== 2 || button.getAttribute("aria-label") !== "Pause")
    throw new Error("paused playback was not recovered");
  for (let i = 0; i < 3; i++) runTimer(400);
  if (clicks !== 2) throw new Error("recovered playback was interrupted");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_unavailable_is_one_transition_and_cancels_pending_actions(tmp_path):
    run_browser_test(tmp_path, """
const running = {get: 100, accel: 1, running: true, paused: false, seq: 1};
const unavailable = {get: 100, accel: 1, running: false, paused: null, seq: 1};
const states = [running, unavailable, unavailable, unavailable, running, unavailable, unavailable, running];
let nextState = 0;
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({ok: true, state: states[nextState++]})}};
""", """
  if (clicks !== 1) throw new Error("play-first did not start");
  runTimer(250);
  if (clicks !== 2) throw new Error("unavailable did not pause once");
  button.setAttribute("aria-label", "Pause"); // site update/user changes playback while unavailable
  runTimer(250);
  runTimer(500); // obsolete pending seek
  runTimer(250);
  if (clicks !== 2 || seeks !== 0) throw new Error("repeated unavailable acted again");
  button.setAttribute("aria-label", "Play");
  runTimer(250); // reconnect
  if (clicks !== 3) throw new Error("reconnect did not resume");
  runTimer(500);
  if (seeks !== 1) throw new Error("reconnect did not seek");
  runTimer(250); // disconnect while post-seek retry is pending
  if (clicks !== 4) throw new Error("second disconnect did not pause");
  runTimer(400); // obsolete retry must not play
  runTimer(500); // obsolete seek verification
  runTimer(250);
  if (clicks !== 4 || seeks !== 1) throw new Error("retry ran while unavailable");
  runTimer(250); // reconnect again
  runTimer(500);
  if (clicks !== 5 || seeks !== 2) throw new Error("second reconnect failed");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_unavailable_cancels_play_first_and_transport_failures_are_latched(tmp_path):
    run_browser_test(tmp_path, """
let callback;
window.chrome = {runtime: {sendMessage: (_msg, cb) => { callback = cb; }}};
""", """
  let delayedClicks = 0;
  button.onclick = () => { clicks++; delayedClicks++; }; // site has not reported playing yet
  callback({ok: true, state: {get: 100, accel: 1, running: true, paused: false, seq: 1}});
  if (delayedClicks !== 1) throw new Error("play-first did not start");
  runTimer(250); // pending play-first retry
  if (delayedClicks !== 2) throw new Error("play-first retry missing");
  runTimer(250); // issue next bridge request
  callback({ok: false});
  runTimer(250); // stale play-first retry
  if (delayedClicks !== 2) throw new Error("play-first retried after disconnect");
  runTimer(250);
  button.setAttribute("aria-label", "Pause");
  callback({ok: false});
  if (delayedClicks !== 2) throw new Error("repeated transport failure acted again");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_reconnect_at_timescale_resumes_without_seeking(tmp_path):
    run_browser_test(tmp_path, """
const states = [
  {get: 100, accel: 1, running: true, paused: false, seq: 1},
  {get: 100, accel: 1, running: false, paused: null, seq: 1},
  {get: 100, accel: 4, running: false, paused: false, seq: 1}
];
let nextState = 0;
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({ok: true, state: states[nextState++]})}};
""", """
  runTimer(250);
  if (clicks !== 2) throw new Error("unavailable did not pause");
  runTimer(250);
  if (clicks !== 3 || seeks !== 0 || button.getAttribute("aria-label") !== "Pause")
    throw new Error("timescale reconnect did not resume without seeking");
  runTimer(500); // obsolete initial seek
  if (seeks !== 0) throw new Error("obsolete seek was not cancelled");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_poll_timeout_discards_late_response(tmp_path):
    run_browser_test(tmp_path, """
const callbacks = [];
window.chrome = {runtime: {sendMessage: (_msg, cb) => callbacks.push(cb)}};
const running = {get: 100, accel: 1, running: true, paused: false, seq: 1};
""", """
  callbacks[0]({ok: true, state: running});
  runTimer(500); // seek and schedule play retry
  runTimer(250); // a new request hangs
  runTimer(1500); // timeout marks unavailable
  if (clicks !== 2) throw new Error("timeout did not pause playback");
  runTimer(400); // stale play retry
  runTimer(500); // stale seek verification
  callbacks[1]({ok: true, state: running}); // delayed stale reply
  if (clicks !== 2 || seeks !== 1) throw new Error("late response restarted playback");
  runTimer(250);
  callbacks[2]({ok: true, state: running});
  runTimer(500);
  if (clicks !== 3 || seeks !== 2) throw new Error("fresh response did not resync");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_disconnect_after_pause_does_not_repeat_pending_pause(tmp_path):
    run_browser_test(tmp_path, """
const states = [
  {get: 100, accel: 1, running: true, paused: false, seq: 1},
  {get: 100, accel: 1, running: false, paused: true, seq: 1},
  {get: 100, accel: 1, running: false, paused: null, seq: 1}
];
let nextState = 0;
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({ok: true, state: states[nextState++]})}};
""", """
  button.onclick = () => { clicks++; }; // delayed UI response to pause
  runTimer(250);
  if (clicks !== 2) throw new Error("pause command missing");
  runTimer(250);
  if (clicks !== 2) throw new Error("disconnect repeated pending pause");
  runTimer(500); // obsolete seek
  if (seeks !== 0) throw new Error("pending seek survived disconnect");
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
@pytest.mark.parametrize("updates_label", [True, False])
def test_real_site_markup_resume_pause_and_disconnect_with_delayed_player(tmp_path, updates_label):
    run_browser_test(tmp_path, f"""
const updatesLabel = {str(updates_label).lower()};
""" + """
let callback;
window.chrome = {runtime: {sendMessage: (_msg, cb) => { callback = cb; }}};
const paused = {get: 100, accel: 1, running: false, paused: true, seq: 1};
const running = {get: 101, accel: 1, running: true, paused: false, seq: 2};
const unavailable = {get: 101, accel: 1, running: false, paused: null, seq: 2};
""", """
  let actualPlaying = true;
  button.onclick = () => {
    clicks++;
    const requestPlay = !button.classList.contains("pause");
    // The real site acts on the class; YouTube reports the new state later.
    setTimeout(() => {
      actualPlaying = requestPlay;
      button.classList.toggle("pause", actualPlaying);
      if (updatesLabel) document.getElementById("playPauseBtnLabel").textContent = actualPlaying ? "PAUSE" : "PLAY";
    }, 350);
  };
  callback({ok: true, state: paused});
  if (clicks !== 1) throw new Error("initial Orbiter pause did not pause playing site");
  runTimer(350);
  if (actualPlaying) throw new Error("site did not pause");
  runTimer(250);
  callback({ok: true, state: running});
  if (clicks !== 2) throw new Error("resume did not request play");
  runTimer(250); // retry while player response is pending
  runTimer(350);
  if (!actualPlaying) throw new Error("site did not play on resume");
  runTimer(250); // next bridge poll
  callback({ok: true, state: running});
  runTimer(250); // retry observes playing
  runTimer(350); // delayed response to the retry
  runTimer(500); // seek
  if (seeks !== 1) throw new Error("resume did not seek");
  runTimer(250); // request state
  callback({ok: true, state: paused});
  runTimer(350);
  if (actualPlaying) throw new Error("second real pause did not pause site");
  runTimer(250);
  callback({ok: true, state: unavailable});
  const atDisconnect = clicks;
  runTimer(250);
  callback({ok: true, state: unavailable});
  runTimer(400); // stale post-seek retry
  if (clicks !== atDisconnect || actualPlaying) throw new Error("disconnect issued another command");
""", markup="""
<a id="playPauseBtn" class="splash-btn subdued pause" title="Play/Pause">
  <span id="playPauseBtnLabel">PAUSE</span><span class="sr-only">Play/Pause</span>
</a><input id="missionElapsedTime"><button id="GETBtn"></button><pre id="result">NOT RUN</pre>
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_real_site_pending_play_is_paused_once_after_disconnect(tmp_path):
    run_browser_test(tmp_path, """
let callback;
window.chrome = {runtime: {sendMessage: (_msg, cb) => { callback = cb; }}};
const running = {get: 100, accel: 1, running: true, paused: false, seq: 1};
const unavailable = {get: 100, accel: 1, running: false, paused: null, seq: 1};
""", """
  let actualPlaying = false;
  button.onclick = () => {
    clicks++;
    const requestPlay = !button.classList.contains("pause");
    setTimeout(() => {
      actualPlaying = requestPlay;
      button.classList.toggle("pause", actualPlaying);
    }, 350);
  };
  callback({ok: true, state: running});
  if (clicks !== 1) throw new Error("resume did not request play");
  runTimer(250); // pending play retry
  runTimer(250); // bridge request
  callback({ok: true, state: unavailable});
  const beforeAck = clicks;
  runTimer(350); // player acknowledges earlier play
  runTimer(250); // cancelled play retry
  runTimer(250); // guard sees playback and pauses once
  if (clicks !== beforeAck + 1) throw new Error("late playback was not paused");
  runTimer(350); // second play acknowledgement
  runTimer(350); // pause acknowledgement
  runTimer(250); // repeated unavailable
  callback({ok: true, state: unavailable});
  if (clicks !== beforeAck + 1 || actualPlaying) throw new Error("unavailable toggled playback");
""", markup="""
<a id="playPauseBtn" class="splash-btn subdued" title="Play/Pause">
  <span id="playPauseBtnLabel">PLAY</span><span class="sr-only">Play/Pause</span>
</a><input id="missionElapsedTime"><button id="GETBtn"></button><pre id="result">NOT RUN</pre>
""")


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
@pytest.mark.parametrize("orbiter_start,drop_first", [(0, False), (4000, False), (0, True)])
def test_initial_sync_waits_for_ready_and_confirms_site_get(tmp_path, orbiter_start, drop_first):
    script = (Path(__file__).parent.parent / "browser_extension" / "content.js").read_text(encoding="utf-8")
    html = ("""
<a id="playPauseBtn" class="pause"><span id="playPauseBtnLabel">PAUSE</span>
<span class="sr-only">Play/Pause</span></a>
<input id="missionElapsedTime" name="missionElapsedTime" value="-03:59:59">
<a id="GETBtn"></a><pre id="result">NOT RUN</pre>
<script>
let clock = 0, id = 0, queue = [], seeks = 0, observed = "-03:59:59";
let errors = [];
window.onerror = (message) => { errors.push(String(message)); };
const orbiterStart = START_AT;
let dropFirst = DROP_FIRST;
window.setTimeout = (fn, delay) => { const task = {fn, at: clock + delay, id: ++id}; queue.push(task); return task.id; };
window.clearTimeout = n => { queue = queue.filter(t => t.id !== n); };
Object.defineProperty(performance, "now", {value: () => clock});
const button = document.getElementById("playPauseBtn");
const input = document.getElementById("missionElapsedTime");
button.onclick = () => setTimeout(() => button.classList.toggle("pause"), 80);
document.getElementById("GETBtn").onclick = () => {
  if (!document.body.classList.contains("app-ready")) throw new Error("seek before readiness");
  seeks++;
  const requested = input.value;
  if (dropFirst) { dropFirst = false; return; }
  setTimeout(() => { observed = requested; }, 700);
};
function siteRefresh() {
  if (document.activeElement !== input) input.value = observed;
  setTimeout(siteRefresh, 500);
}
siteRefresh();
setTimeout(() => {
  observed = "-03:59:59"; // initializePlayback's URL seek occurs before app-ready
  document.body.classList.add("app-ready");
}, 2000);
window.chrome = {runtime: {sendMessage: (_msg, cb) => {
  const state = clock < orbiterStart
    ? {get: null, paused: null, running: false, accel: 1, seq: 0}
    : {get: 186634, paused: false, running: true, accel: 1, seq: 1};
  setTimeout(() => cb({ok: true, state}), 30);
}}};
function advance(until) {
  while (true) {
    queue.sort((a, b) => a.at - b.at || a.id - b.id);
    if (!queue.length || queue[0].at > until) break;
    const task = queue.shift(); clock = task.at; task.fn();
  }
  clock = until;
}
</script>
<script>
""" + script + """
</script>
<script>
try {
  advance(1900);
  const badge = document.querySelector("html > div");
  if (seeks || badge.textContent.includes("synced to") ||
      badge.textContent.includes("following Orbiter"))
    throw new Error("claimed sync before readiness");
  advance(2300);
  if (seeks && badge.textContent.includes("synced to"))
    throw new Error("claimed sync before site's GET refresh");
  advance(10000);
  if (observed !== "51:50:34" && !observed.startsWith("51:50:"))
    throw new Error("site never reached requested GET: " + observed + ", seeks=" + seeks + ", badge=" + badge.textContent + ", errors=" + errors);
  if (!badge.textContent.includes("following Orbiter") &&
      !badge.textContent.includes("synced to"))
    throw new Error("site GET was not confirmed");
  if (DROP_EXPECT && seeks < 2) throw new Error("failed seek was not retried");
  document.getElementById("result").textContent = "PASS";
} catch (e) { document.getElementById("result").textContent = "FAIL: " + e; }
</script>
""").replace("START_AT", str(orbiter_start)).replace("DROP_FIRST", str(drop_first).lower()).replace(
        "DROP_EXPECT", str(drop_first).lower()
    )
    page = tmp_path / "startup.html"
    page.write_text(html, encoding="utf-8")
    result = subprocess.run(
        [str(CHROME), "--headless", "--disable-gpu", "--no-first-run",
         f"--user-data-dir={tmp_path / 'profile'}", "--dump-dom", page.as_uri()],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert '<pre id="result">PASS</pre>' in result.stdout, result.stdout


@pytest.mark.skipif(os.name != "nt" or not CHROME.exists(), reason="Chrome required")
def test_initial_paused_waits_for_page_then_pauses(tmp_path):
    run_browser_test(tmp_path, """
document.body.classList.remove("app-ready");
window.chrome = {runtime: {sendMessage: (_msg, cb) => cb({
  ok: true, state: {get: 100, accel: 1, running: false, paused: true, seq: 1}
})}};
""", """
  button.setAttribute("aria-label", "Pause"); // site begins playing once loaded
  runTimer(250);
  if (clicks !== 0) throw new Error("clicked before the site was ready");
  document.body.classList.add("app-ready");
  runTimer(250);
  if (clicks !== 1 || button.getAttribute("aria-label") !== "Play")
    throw new Error("initial Orbiter pause was not applied");
""")
