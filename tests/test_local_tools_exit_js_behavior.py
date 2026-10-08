import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "tools" / "browser_extensions" / "local_tools_exit"
NODE = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const extensionDir = process.argv[1];
const scenario = process.argv[2];
const backgroundSource = fs.readFileSync(`${extensionDir}/background.js`, "utf8");
const contentSource = fs.readFileSync(`${extensionDir}/content.js`, "utf8");
const dashboardSource = fs.readFileSync(path.resolve(extensionDir, "../../../render/static/dashboard.js"), "utf8");

function tab(id, url, windowId, pendingUrl) {
  const value = { id, url, windowId };
  if (pendingUrl) value.pendingUrl = pendingUrl;
  return value;
}

async function createPage(mode) {
  const tabs = new Map([
    [1, tab(1, "http://127.0.0.1:8000/instrument-lookup", 10)],
    [2, tab(2, "http://localhost:8000/dashboard", 10)],
    [3, tab(3, "http://127.0.0.1:8000/chart/gbpusd", 20)],
    [4, tab(4, "http://localhost:8001/other-local-app", 20)],
    [5, tab(5, "http://localhost.evil:8000/fake", 20)],
    [6, tab(6, "https://example.com/local-tools", 30)],
    [7, tab(7, "http://localhost:8000/old-page", 30, "https://example.com/next")]
  ]);
  const events = [];
  let onMessage;
  const requester = () => tabs.get(1);
  const sender = () => ({ tab: requester(), frameId: 0, url: requester().url });
  const button = { disabled: true, textContent: "Exit local tools", title: "" };
  const status = { textContent: "Install the Local Tools Exit browser extension to close all tool tabs." };
  let buildInfoRequests = 0;
  let clickHandler;
  const document = {
    querySelector(selector) {
      if (selector === "#local-exit-control button") return button;
      if (selector === "#local-exit-control-status") return status;
      return null;
    },
    addEventListener(type, handler, capture) {
      assert.equal(type, "click");
      assert.equal(capture, true);
      clickHandler = handler;
    }
  };
  const chrome = {
    runtime: {
      onMessage: { addListener(listener) { onMessage = listener; } },
      sendMessage(message) {
        if (mode === "extension_unavailable") return Promise.reject(new Error("Receiving end does not exist."));
        const actualSender = sender();
        return new Promise((resolve) => {
          const keepOpen = onMessage(message, actualSender, resolve);
          if (!keepOpen) resolve(undefined);
        });
      }
    },
    tabs: {
      async query() {
        events.push("tabs:query");
        const snapshot = [...tabs.values()].map((value) => ({ ...value }));
        if (mode === "navigate_candidate") tabs.set(3, tab(3, "https://example.com/navigated", 20));
        return snapshot;
      },
      async get(id) {
        const value = tabs.get(id);
        if (!value) throw new Error("No tab with id: " + id);
        return { ...value };
      },
      async remove(id) {
        events.push(`tabs:remove:${id}`);
        tabs.delete(id);
      }
    }
  };
  const fetch = async (url, options = {}) => {
    if (String(url).endsWith("/api/local-build-info")) {
      buildInfoRequests += 1;
      events.push("api:build-info");
      if (mode === "transient_probe_timeout" && buildInfoRequests === 1) {
        throw new Error("The operation timed out.");
      }
      return { ok: true, status: 200, async json() { return { app_profile: mode === "wrong_profile" ? "render" : "local", pid: 4321 }; } };
    }
    if (String(url).endsWith("/api/local-exit")) {
      events.push("api:exit-started");
      assert.equal(options.method, "POST");
      if (mode === "rejected") return { ok: false, status: 503, async json() { return {}; } };
      events.push("api:exit-accepted");
      return { ok: true, status: 200, async json() { return { ok: true, action: "local_exit" }; } };
    }
    throw new Error("unexpected fetch: " + url);
  };
  const backgroundContext = { chrome, fetch, URL, AbortSignal, Set, Number, String, Error, Promise, console };
  vm.runInNewContext(backgroundSource, backgroundContext, { filename: "background.js" });
  const pageUrl = new URL("http://127.0.0.1:8000/instrument-lookup");
  const window = {};
  window.top = window;
  window.self = window;
  window.setTimeout = setTimeout;
  window.clearTimeout = clearTimeout;
  const windowEvents = {};
  window.addEventListener = (type, handler) => { windowEvents[type] = handler; };
  const contentContext = {
    chrome, document, window, location: pageUrl, URL, Set, Promise, Error, console, setTimeout, clearTimeout
  };
  vm.runInNewContext(contentSource, contentContext, { filename: "content.js" });
  await new Promise((resolve) => setImmediate(resolve));

  function click(isTrusted = true) {
    const event = {
      target: { closest(selector) { return selector === "#local-exit-control button" ? button : null; } },
      isTrusted,
      prevented: false,
      stopped: false,
      preventDefault() { this.prevented = true; },
      stopImmediatePropagation() { this.stopped = true; }
    };
    clickHandler(event);
    return event;
  }
  return { tabs, events, button, status, click, sender, onMessage, windowEvents };
}

async function success() {
  const page = await createPage("accepted");
  assert.equal(page.button.disabled, false, "helper is ready after local ownership check");
  const first = page.click(true);
  const duplicate = page.click(true);
  assert.equal(first.prevented, true);
  assert.equal(first.stopped, true);
  assert.equal(duplicate.prevented, true);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(page.events.filter((e) => e.startsWith("tabs:remove:")).sort(), ["tabs:remove:1", "tabs:remove:2", "tabs:remove:3"]);
  assert.ok(page.events.indexOf("api:exit-accepted") < page.events.indexOf("tabs:remove:2"), "shutdown response must finish before tab removal");
  assert.ok(page.tabs.has(4) && page.tabs.has(5) && page.tabs.has(6) && page.tabs.has(7), "unrelated, other-port, lookalike and navigated-away tabs remain");
  assert.equal(page.events.filter((e) => e === "api:exit-started").length, 1, "duplicate click sends only one exit request");
  assert.match(page.status.textContent, /tabs closed/i);
}

async function failures() {
  const rejected = await createPage("rejected");
  rejected.click(true);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(rejected.events.filter((e) => e.startsWith("tabs:remove:")).length, 0);
  assert.equal(rejected.button.disabled, false, "a rejected shutdown leaves the action recoverable");
  assert.match(rejected.status.textContent, /failed/i);

  const navigated = await createPage("accepted");
  navigated.tabs.set(1, tab(1, "https://example.com/left", 10));
  navigated.click(true);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(navigated.events.filter((e) => e === "api:exit-started").length, 0);
  assert.equal(navigated.events.filter((e) => e.startsWith("tabs:remove:")).length, 0);

  const invalid = await createPage("accepted");
  const invalidReply = await new Promise((resolve) => {
    invalid.onMessage({ type: "exit" }, { tab: invalid.sender().tab, frameId: 1, url: invalid.sender().url }, resolve);
  });
  assert.equal(invalidReply.ok, false);
  assert.equal(invalid.events.filter((e) => e === "api:exit-started").length, 0);
  assert.equal(invalid.events.filter((e) => e.startsWith("tabs:remove:")).length, 0);

  const foreignApp = await createPage("wrong_profile");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(foreignApp.button.disabled, true);
  assert.match(foreignApp.status.textContent, /could not verify|did not identify/i);
  assert.equal(foreignApp.events.filter((e) => e === "api:exit-started").length, 0);

  const navigatedCandidate = await createPage("navigate_candidate");
  navigatedCandidate.click(true);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(navigatedCandidate.events.includes("tabs:remove:3"), false, "current URL is rechecked before removing each tab");
  assert.equal(navigatedCandidate.tabs.get(3).url, "https://example.com/navigated");

  const unavailable = await createPage("extension_unavailable");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(unavailable.button.disabled, true);
  assert.match(unavailable.status.textContent, /unavailable/i);
  assert.equal(unavailable.events.filter((e) => e === "api:exit-started").length, 0);
}

async function dashboardProbeRecovery() {
  const page = await createPage("transient_probe_timeout");
  const elements = new Map();
  const makeElement = (id) => ({
    id,
    children: [],
    style: {},
    dataset: {},
    addEventListener(type, handler) { this[`on${type}`] = handler; },
    appendChild(child) { this.children.push(child); return child; },
    set innerHTML(_value) { this.children = []; },
    get innerHTML() { return ""; },
    setAttribute() {},
  });
  ["refresh-btn", "status", "scripts-grid", "exit-button-slot"].forEach((id) => elements.set(id, makeElement(id)));
  const document = {
    body: { dataset: { dashboardProfile: "local" } },
    visibilityState: "visible",
    getElementById(id) { return elements.get(id) || null; },
    createElement(tag) { const node = makeElement(tag); node.tagName = tag; return node; },
    addEventListener() {},
  };
  const dashboardWindow = { addEventListener() {}, open() { throw new Error("dashboard must not open tabs during refresh"); } };
  const dashboardFetch = async (url) => ({
    ok: true,
    status: 200,
    statusText: "OK",
    async text() { return url === "/api/pine/files" ? JSON.stringify({ files: [] }) : "[]"; },
  });
  vm.runInNewContext(dashboardSource, {
    document, window: dashboardWindow, fetch: dashboardFetch, URL, Date, Map, Set, String,
    Number, Math, JSON, Error, Promise, Array, Object, console,
    setInterval() { return 1; }, clearInterval() {}, setTimeout, clearTimeout,
  }, { filename: "dashboard.js" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  await elements.get("refresh-btn").onclick();
  await new Promise((resolve) => setTimeout(resolve, 20));

  const dashboardButtons = [
    ...elements.get("scripts-grid").children,
    ...elements.get("exit-button-slot").children,
  ].filter((node) => String(node.className || "").includes("local-exit-btn"));
  assert.equal(dashboardButtons.length, 0, "local dashboard refreshes must not recreate the legacy direct-POST Exit button");
  assert.equal(page.events.filter((event) => event === "api:exit-started").length, 0, "readiness retries never request shutdown");

  const startedAt = Date.now();
  while (page.button.disabled && Date.now() - startedAt < 2500) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.equal(page.events.filter((event) => event === "api:build-info").length, 2, "one transient timeout is followed by one bounded successful probe");
  assert.equal(page.button.disabled, false, "the shared extension path becomes usable after verification");
  assert.equal(page.events.filter((event) => event === "api:exit-started").length, 0, "a successful probe still does not initiate exit");

  page.click(true);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(page.events.filter((event) => event === "api:exit-started").length, 1, "one trusted click sends exactly one exit request");
  assert.ok(page.events.indexOf("api:exit-accepted") < page.events.indexOf("tabs:remove:2"));
}

({
  success,
  failures,
  dashboard_probe_recovery: dashboardProbeRecovery,
}[scenario] || (() => Promise.reject(new Error("unknown scenario: " + scenario))))().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
'''


def _run_node(scenario: str) -> None:
    result = subprocess.run(
        ["node", "-e", NODE, str(EXTENSION), scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, f"Node fixture failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"


def test_exit_closes_all_local_tabs_across_windows_and_preserves_unrelated_tabs() -> None:
    _run_node("success")


def test_exit_failure_and_invalid_sender_do_not_close_tabs_or_claim_success() -> None:
    _run_node("failures")


def test_dashboard_probe_recovers_after_timeout_without_duplicate_exit() -> None:
    _run_node("dashboard_probe_recovery")
