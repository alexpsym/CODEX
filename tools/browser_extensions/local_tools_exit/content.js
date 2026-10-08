(() => {
  "use strict";

  const allowedHosts = new Set(["localhost", "127.0.0.1"]);
  const current = new URL(location.href);
  if (window.top !== window.self || current.protocol !== "http:" || !allowedHosts.has(current.hostname.toLowerCase()) || current.port !== "8000" || current.username || current.password) return;

  const button = document.querySelector("#local-exit-control button");
  const status = document.querySelector("#local-exit-control-status");
  if (!button || !status) return;

  let ready = false;
  let busy = false;
  let probing = false;
  let probeAttempts = 0;
  let retryTimer = null;
  let pageClosed = false;
  const MAX_PROBE_ATTEMPTS = 4;
  const RETRY_DELAY_MS = 900;
  const setStatus = (message) => { status.textContent = message; };

  const errorMessage = (error) => String(error && error.message ? error.message : error || "Check failed.");
  const updateReady = () => {
    ready = true;
    button.disabled = false;
    button.textContent = "Exit local tools";
    button.title = "Request local worker shutdown and close Local Trading Tools tabs in this browser profile.";
    setStatus("Exit helper and local worker verified.");
  };
  const runProbe = async () => {
    if (probing || pageClosed || ready) return;
    probing = true;
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
    button.disabled = true;
    button.textContent = "Checking exit...";
    probeAttempts += 1;
    setStatus(`Checking exit helper and local worker (${probeAttempts}/${MAX_PROBE_ATTEMPTS})...`);
    let failure = null;
    try {
      const reply = await chrome.runtime.sendMessage({ type: "probe" });
      if (!reply || reply.ok !== true || reply.ready !== true) throw new Error(reply && reply.error ? reply.error : "Local worker was not verified.");
      updateReady();
    } catch (error) {
      failure = errorMessage(error);
    } finally {
      probing = false;
    }
    if (ready || pageClosed) return;
    const localUnavailable = /timed? ?out|timeout|aborterror|failed to fetch|networkerror|local app check failed/i.test(failure || "");
    const identityRejected = /did not identify|outside the supported/i.test(failure || "");
    const extensionUnavailable = /receiving end does not exist|could not establish connection|extension context invalidated/i.test(failure || "");
    const prefix = localUnavailable
      ? "Local worker did not respond"
      : (identityRejected
        ? "Exit helper could not verify this local worker"
        : (extensionUnavailable ? "Browser extension is unavailable in this tab" : "Exit helper check failed"));
    if (probeAttempts < MAX_PROBE_ATTEMPTS) {
      setStatus(`${prefix}; retrying (${probeAttempts}/${MAX_PROBE_ATTEMPTS}).`);
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        runProbe();
      }, RETRY_DELAY_MS);
    } else {
      button.disabled = false;
      button.textContent = "Recheck exit";
      setStatus(`${prefix}. Click Recheck exit to try again.`);
    }
  };
  const cancelProbe = () => {
    pageClosed = true;
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    retryTimer = null;
  };
  window.addEventListener("pagehide", cancelProbe, { once: true });
  runProbe();

  document.addEventListener("click", (event) => {
    const clicked = event.target && event.target.closest ? event.target.closest("#local-exit-control button") : null;
    if (clicked !== button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (busy) return;
    if (!event.isTrusted) {
      setStatus("Use the Exit button directly to close local tools.");
      return;
    }
    if (!ready) {
      probeAttempts = 0;
      runProbe();
      return;
    }
    busy = true;
    button.disabled = true;
    button.textContent = "Exiting...";
    setStatus("Requesting the local worker to exit...");
    chrome.runtime.sendMessage({ type: "exit" }).then((reply) => {
      if (!reply || reply.ok !== true || reply.closed !== true) throw new Error(reply && reply.error ? reply.error : "Exit was not completed.");
      setStatus("Local Trading Tools tabs closed.");
    }).catch((error) => {
      busy = false;
      button.disabled = !ready;
      button.textContent = "Exit local tools";
      setStatus(`Exit failed: ${error && error.message ? error.message : error}`);
    });
  }, true);
})();
