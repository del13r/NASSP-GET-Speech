// Drives apolloinrealtime.org from the Orbiter GET published by NASSP GET Speech.
// Orbiter paused -> pause the site.  Orbiter running at 1x -> play, then seek to the GET.
(function () {
  const POLL_MS = 250;
  const POLL_TIMEOUT_MS = 1500;
  const PLAY_POLLS = 10;
  const PLAY_POLL_MS = 400;
  const PLAY_FIRST_MS = 500;
  const PLAY_FIRST_TRIES = 8;
  const PENDING_PLAY_CHECK_MS = 250;
  const SEEK_CHECK_MS = 500;
  const SEEK_CHECKS = 8;

  let lastMode = null;
  let lastSeq = null;
  let playTries = 0;
  let generation = 0;
  let pendingPlay = false;

  const badge = document.createElement("div");
  badge.style.cssText =
    "position:fixed;left:8px;bottom:8px;z-index:2147483647;padding:3px 8px;border-radius:6px;" +
    "font:12px sans-serif;color:#fff;background:#444;opacity:.85;pointer-events:none";
  document.documentElement.appendChild(badge);
  const say = (text, color) => {
    badge.textContent = "NASSP GET Speech: " + text;
    badge.style.background = color || "#444";
  };

  function formatTime(seconds) {
    const total = Math.floor(seconds);
    const sign = total < 0 ? "-" : "";
    const a = Math.abs(total);
    const p = (n) => String(n).padStart(2, "0");
    return sign + p(Math.floor(a / 3600)) + ":" + p(Math.floor((a % 3600) / 60)) + ":" + p(a % 60);
  }

  function parseTime(text) {
    const match = /^([+-]?)(\d+):([0-5]\d):([0-5]\d)$/.exec(text.trim());
    if (!match) return null;
    const total = Number(match[2]) * 3600 + Number(match[3]) * 60 + Number(match[4]);
    return match[1] === "-" ? -total : total;
  }

  const playBtn = () => document.getElementById("playPauseBtn");
  const isPlaying = () => {
    const p = playBtn();
    if (!p) return null;
    if (p.querySelector("#playPauseBtnLabel")) return p.classList.contains("pause");
    const label = (p.getAttribute("aria-label") || p.textContent).trim().toLowerCase();
    if (label === "pause") return true;
    if (label === "play") return false;
    return null;
  };

  function pauseSite() {
    playTries = 0;
    const token = generation;
    const pauseWhenPlaying = () => {
      if (token !== generation) return;
      if (isPlaying() === true) {
        pendingPlay = false;
        playBtn().click();
      } else if (pendingPlay) {
        setTimeout(pauseWhenPlaying, PENDING_PLAY_CHECK_MS);
      }
    };
    pauseWhenPlaying();
  }

  function requestPlay() {
    pendingPlay = true;
    playBtn().click();
  }

  function unavailable(text, color) {
    if (lastMode !== "unavailable") {
      generation++;
      if (lastMode !== "paused" || pendingPlay) pauseSite();
      lastMode = "unavailable";
    }
    say(text, color);
  }

  function goButton() {
    const go = document.getElementById("GETBtn");
    if (go) return go;
    for (const b of document.querySelectorAll("button, input[type=submit], [role=button]")) {
      if ((b.textContent || b.value || "").trim().toUpperCase() === "GO") return b;
    }
    return null;
  }

  function pollPlay(token) {
    if (token !== generation || playTries <= 0) return;
    playTries--;
    const playing = isPlaying();
    if (playing === false) {
      requestPlay();
    } else if (playing === true) {
      pendingPlay = false;
    }
    setTimeout(() => pollPlay(token), PLAY_POLL_MS);
  }

  function seekTo(seconds, token) {
    if (token !== generation) return;
    const box = document.getElementById("missionElapsedTime");
    const go = goButton();
    if (!box || !go || !document.body.classList.contains("app-ready")) {
      lastMode = null;
      return;
    }
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    box.focus();
    setter.call(box, formatTime(seconds));
    box.dispatchEvent(new Event("input", { bubbles: true }));
    go.click();
    // The site periodically replaces this unfocused field with its own GET.
    setter.call(box, "");
    box.blur();
    playTries = PLAY_POLLS;
    setTimeout(() => pollPlay(token), PLAY_POLL_MS);
    let remaining = SEEK_CHECKS;
    const check = () => {
      if (token !== generation) return;
      const current = parseTime(box.value);
      if (current !== null && Math.abs(current - liveGet()) <= 5 && isPlaying() === true) {
        lastMode = "running";
        say("synced to " + formatTime(current), "#1f7a3d");
      } else if (--remaining > 0) {
        setTimeout(check, SEEK_CHECK_MS);
      } else {
        generation++;
        lastMode = null;
        say("seek not confirmed, retrying", "#8a6d1f");
      }
    };
    setTimeout(check, SEEK_CHECK_MS);
  }

  // Play first: keep clicking Play until the site reports playing, then seek to the live GET.
  function syncTo() {
    if (!document.body.classList.contains("app-ready") ||
        !document.getElementById("missionElapsedTime") || !goButton() || !playBtn()) return false;
    const token = ++generation;
    let tries = 0;
    const step = () => {
      if (token !== generation) return;
      tries++;
      if (isPlaying() === false) requestPlay();
      if (isPlaying() === true || tries >= PLAY_FIRST_TRIES) {
        if (isPlaying() === true) pendingPlay = false;
        setTimeout(() => {
          if (token === generation) seekTo(liveGet(), token);
        }, PLAY_FIRST_MS);
      } else {
        setTimeout(step, 250);
      }
    };
    step();
    return true;
  }

  let latest = null;
  let latestAt = 0;
  const liveGet = () =>
    latest.get + (latest.running ? ((performance.now() - latestAt) / 1000) * latest.accel : 0) + (latest.offset || 0);

  function handle(state) {
    latest = state;
    latestAt = performance.now();
    if (state.get === null || state.paused == null) {
      return unavailable(state.get === null ? "waiting for GET from Orbiter" : "pause state or GET unavailable", "#8a6d1f");
    }
    if (state.paused) {
      if (!document.body.classList.contains("app-ready")) {
        if (lastMode !== "paused-waiting") generation++;
        lastMode = "paused-waiting";
        return say("waiting for page to pause", "#8a6d1f");
      }
      if (lastMode !== "paused") {
        generation++;
        pauseSite();
      }
      lastMode = "paused";
      return say("Orbiter paused at " + formatTime(state.get), "#8a6d1f");
    }
    if (!state.running) {
      if (lastMode !== "timescale") generation++;
      // Timescaled: keep playing (drifts from Orbiter); a return to 1x re-syncs.
      if ((lastMode === "paused" || lastMode === "unavailable") && isPlaying() === false) requestPlay();
      lastMode = "timescale";
      return say("timescaled x" + state.accel + " (back to 1x to re-sync)", "#8a6d1f");
    }
    if (lastMode === "syncing" && state.seq === lastSeq) {
      return say("syncing to Orbiter", "#8a6d1f");
    }
    if (lastMode !== "running" || state.seq !== lastSeq) {
      if (!syncTo()) {
        generation++;
        lastMode = null;
        return say("page not ready, retrying", "#8a6d1f");
      }
      lastMode = "syncing";
      lastSeq = state.seq;
      return say("syncing to Orbiter", "#8a6d1f");
    }
    say("following Orbiter " + formatTime(state.get), "#1f7a3d");
  }

  function poll() {
    let settled = false;
    const timeout = setTimeout(() => finish(null), POLL_TIMEOUT_MS);
    function finish(res) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (!res || !res.ok) {
        unavailable("app not reachable", "#8a2f2f");
      } else {
        handle(res.state);
      }
      setTimeout(poll, POLL_MS);
    }
    chrome.runtime.sendMessage({ type: "state" }, (res) => {
      finish(chrome.runtime.lastError ? null : res);
    });
  }

  say("starting");
  poll();
})();
