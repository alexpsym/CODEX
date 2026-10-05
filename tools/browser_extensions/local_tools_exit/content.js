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
  const setStatus = (message) => { status.textContent = message; };

  chrome.runtime.sendMessage({ type: "probe" }).then((reply) => {
    if (!reply || reply.ok !== true || reply.ready !== true) throw new Error(reply && reply.error ? reply.error : "Browser helper unavailable.");
    ready = true;
    button.disabled = false;
    button.title = "Request local worker shutdown and close Local Trading Tools tabs in this browser profile.";
    setStatus("Closes Local Trading Tools tabs in this browser profile.");
  }).catch((error) => {
    ready = false;
    button.disabled = true;
    setStatus(`Exit helper unavailable: ${error && error.message ? error.message : error}. Reload this page after enabling the extension.`);
  });

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
      setStatus("Exit helper unavailable. Enable or reload the extension, then reload this page.");
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
