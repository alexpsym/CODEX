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

    let logCursor = 0;
    let pollTimer = null;

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
                if (!appFrame.src) {
                    appFrame.src = `/apps/${buildScriptPath(scriptName)}`;
                }
            } else {
                appPanel.style.display = 'none';
                appFrame.src = '';
            }
        }
    };

    const refreshStatus = async () => {
        try {
            const scripts = await fetchJson('/scripts');
            const script = scripts.find((item) => item.name === scriptName);
            setRunningState(Boolean(script && script.running));
        } catch (err) {
            console.error(err);
            statusEl.textContent = 'Unable to load status.';
        }
    };

    const startScript = async () => {
        startBtn.disabled = true;
        logBox.textContent = 'Starting script...\n';
        try {
            const payload = await fetchJson(`/scripts/${buildScriptPath(scriptName)}/start`, { method: 'POST' });
            if (payload?.redirect) {
                window.location.href = payload.redirect;
                return;
            }
            await refreshStatus();
            if (!pollTimer) {
                pollTimer = setInterval(pollLogs, 2000);
            }
        } catch (err) {
            console.error(err);
            alert(err.message || 'Failed to start script');
            startBtn.disabled = false;
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

    refreshStatus();
    pollLogs();
    pollTimer = setInterval(pollLogs, 2000);
})();
