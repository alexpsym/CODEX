(() => {
    const scriptName = document.body.dataset.scriptName;
    const hasUi = document.body.dataset.hasUi === 'true';

    const statusEl = document.getElementById('script-status');
    const startBtn = document.getElementById('start-btn');
    const stopBtn = document.getElementById('stop-btn');
    const logBox = document.getElementById('log-box');
    const appPanel = document.getElementById('app-panel');
    const appFrame = document.getElementById('app-frame');

    const buildScriptPath = (name) => encodeURIComponent(name).replace(/%2F/g, '/');
    const appUrl = `/apps/${buildScriptPath(scriptName)}`;

    let logCursor = 0;
    let pollTimer = null;
    let appLoadTimer = null;
    let appFrameLoaded = false;
    let autoStartInFlight = false;

    const fetchJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) {
            const body = await response.text();
            const detail = body || response.statusText;
            throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}: ${detail}`);
        }
        return response.json();
    };

    const appendLogs = (lines) => {
        if (!lines.length) return;
        const text = lines.join('\n') + '\n';
        if (logBox.textContent === 'Waiting for output...') {
            logBox.textContent = '';
        }
        logBox.textContent += text;
        logBox.scrollTop = logBox.scrollHeight;
    };

    const pollLogs = async () => {
        try {
            const snapshot = await fetchJson(`/api/logs/${buildScriptPath(scriptName)}?cursor=${logCursor}`);
            logCursor = snapshot.cursor ?? logCursor;
            appendLogs(snapshot.lines || []);
        } catch (err) {
            console.error(err);
        }
    };

    const setRunningState = (running) => {
        statusEl.textContent = running ? 'Running' : 'Stopped';
        startBtn.disabled = running;
        stopBtn.disabled = !running;

        if (hasUi) {
            if (running) {
                appPanel.style.display = 'block';
            } else {
                appPanel.style.display = 'none';
                appFrame.src = '';
                appFrameLoaded = false;
            }
        }
    };

    const scheduleAppLoad = () => {
        if (!hasUi || appFrameLoaded || appLoadTimer) {
            return;
        }
        let attempts = 0;
        const maxAttempts = 20;
        const attempt = async () => {
            attempts += 1;
            try {
                const response = await fetch(appUrl, { cache: 'no-store' });
                if (response.ok) {
                    appFrame.src = `${appUrl}?ts=${Date.now()}`;
                    appFrameLoaded = true;
                    appLoadTimer = null;
                    return;
                }
            } catch (err) {
                console.warn('Waiting for app UI...', err);
            }
            if (attempts < maxAttempts) {
                appLoadTimer = setTimeout(attempt, 500);
            } else {
                appLoadTimer = null;
            }
        };
        appLoadTimer = setTimeout(attempt, 200);
    };

    const refreshStatus = async () => {
        try {
            const scripts = await fetchJson('/scripts');
            const script = scripts.find((item) => item.name === scriptName);
            const running = Boolean(script && script.running);
            setRunningState(running);
            if (running) {
                scheduleAppLoad();
            }
            return running;
        } catch (err) {
            console.error(err);
            statusEl.textContent = 'Unable to load status.';
        }
        return false;
    };

    const startScript = async (isAuto = false) => {
        if (autoStartInFlight && isAuto) {
            return;
        }
        autoStartInFlight = isAuto;
        startBtn.disabled = true;
        logBox.textContent = 'Starting script...\n';
        try {
            const payload = await fetchJson(`/scripts/${buildScriptPath(scriptName)}/start`, { method: 'POST' });
            if (payload?.redirect) {
                window.location.href = payload.redirect;
                return;
            }
            let running = await refreshStatus();
            let attempts = 0;
            while (!running && attempts < 10) {
                await new Promise((resolve) => setTimeout(resolve, 500));
                running = await refreshStatus();
                attempts += 1;
            }
            if (!pollTimer) {
                pollTimer = setInterval(pollLogs, 2000);
            }
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to start script');
            startBtn.disabled = false;
        } finally {
            autoStartInFlight = false;
        }
    };

    const stopScript = async () => {
        stopBtn.disabled = true;
        try {
            await fetchJson(`/scripts/${buildScriptPath(scriptName)}/stop`, { method: 'POST' });
            await refreshStatus();
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to stop script');
        } finally {
            stopBtn.disabled = false;
        }
    };

    const backBtn = document.getElementById('nav-back');
    const forwardBtn = document.getElementById('nav-forward');
    backBtn?.addEventListener('click', () => window.history.back());
    forwardBtn?.addEventListener('click', () => window.history.forward());

    startBtn?.addEventListener('click', startScript);
    stopBtn?.addEventListener('click', stopScript);

    const init = async () => {
        const running = await refreshStatus();
        if (!running && hasUi) {
            await startScript(true);
        }
        pollLogs();
        pollTimer = setInterval(pollLogs, 2000);
    };

    init();
})();
