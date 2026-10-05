(() => {
  "use strict";

  const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1"]);
  const LOCAL_PORT = "8000";
  let exitInProgress = false;

  function localOrigin(value) {
    if (typeof value !== "string" || !value) return null;
    try {
      const url = new URL(value);
      if (url.protocol !== "http:" || !LOCAL_HOSTS.has(url.hostname.toLowerCase())) return null;
      if (url.port !== LOCAL_PORT || url.username || url.password) return null;
      return url.origin;
    } catch (_) {
      return null;
    }
  }

  async function senderTab(sender) {
    if (!sender || sender.frameId !== 0 || !sender.tab || !Number.isInteger(sender.tab.id)) {
      throw new Error("Exit request must come from a top-level Local Trading Tools tab.");
    }
    const senderOrigin = localOrigin(sender.url);
    if (!senderOrigin) throw new Error("This tab is outside the supported Local Trading Tools address.");
    const tab = await chrome.tabs.get(sender.tab.id);
    const currentOrigin = localOrigin(tab && tab.url);
    if (!currentOrigin || currentOrigin !== senderOrigin || (tab.pendingUrl && localOrigin(tab.pendingUrl) !== senderOrigin)) {
      throw new Error("The requesting tab navigated away; no tabs were closed.");
    }
    return { tab, origin: senderOrigin };
  }

  async function verifyLocalApplication(origin) {
    const response = await fetch(`${origin}/api/local-build-info`, {
      method: "GET",
      cache: "no-store",
      credentials: "omit",
      signal: AbortSignal.timeout(5000)
    });
    if (!response.ok) throw new Error(`Local app check failed (${response.status}).`);
    const info = await response.json();
    if (!info || info.app_profile !== "local" || typeof info.pid !== "number") {
      throw new Error("The address did not identify the Local Trading Tools worker.");
    }
  }

  async function closeLocalTabs(requesterId) {
    const tabs = await chrome.tabs.query({});
    const candidates = tabs.filter((tab) => localOrigin(tab.url) && (!tab.pendingUrl || localOrigin(tab.pendingUrl)));
    candidates.sort((a, b) => (a.id === requesterId ? 1 : 0) - (b.id === requesterId ? 1 : 0));

    const failures = [];
    for (const candidate of candidates.filter((tab) => tab.id !== requesterId)) {
      try {
        const current = await chrome.tabs.get(candidate.id);
        if (!localOrigin(current.url) || (current.pendingUrl && !localOrigin(current.pendingUrl))) continue;
        await chrome.tabs.remove(candidate.id);
      } catch (error) {
        // A tab that closed itself during the sweep is already in the desired state.
        if (!/no tab|invalid tab id|not found/i.test(String(error && error.message))) {
          failures.push(`tab ${candidate.id}: ${error && error.message ? error.message : error}`);
        }
      }
    }
    if (failures.length) throw new Error(`Some Local Trading Tools tabs could not be closed (${failures.join("; ")}).`);
    try {
      const requester = await chrome.tabs.get(requesterId);
      if (localOrigin(requester.url) && (!requester.pendingUrl || localOrigin(requester.pendingUrl))) {
        await chrome.tabs.remove(requesterId);
      }
    } catch (error) {
      if (!/no tab|invalid tab id|not found/i.test(String(error && error.message))) throw error;
    }
  }

  async function handle(message, sender) {
    const { tab, origin } = await senderTab(sender);
    await verifyLocalApplication(origin);
    if (message && message.type === "probe") return { ok: true, ready: true };
    if (!message || message.type !== "exit") throw new Error("Unknown Local Trading Tools exit request.");
    if (exitInProgress) throw new Error("A Local Trading Tools exit is already being handled.");
    exitInProgress = true;
    try {
      const stillOpen = await chrome.tabs.get(tab.id);
      if (localOrigin(stillOpen.url) !== origin || (stillOpen.pendingUrl && localOrigin(stillOpen.pendingUrl) !== origin)) {
        throw new Error("The requesting tab navigated away; no shutdown was requested.");
      }
      const response = await fetch(`${origin}/api/local-exit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        cache: "no-store",
        credentials: "omit",
        signal: AbortSignal.timeout(5000)
      });
      if (!response.ok) throw new Error(`Local worker rejected exit (${response.status}).`);
      const result = await response.json();
      if (!result || result.ok !== true || result.action !== "local_exit") {
        throw new Error("The local worker did not confirm its exit request.");
      }
      // The shutdown response is complete; tab management no longer depends on the server.
      await closeLocalTabs(tab.id);
      return { ok: true, closed: true };
    } finally {
      exitInProgress = false;
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    handle(message, sender).then(sendResponse, (error) => {
      sendResponse({ ok: false, error: error && error.message ? error.message : String(error) });
    });
    return true;
  });
})();
