// Fetches the GET state from the NASSP GET Speech app on behalf of the page script.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "state") {
    fetch("http://127.0.0.1:8765/", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => sendResponse({ ok: true, state: s }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});
