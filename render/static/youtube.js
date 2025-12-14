(() => {
    const urlInput = document.getElementById('url-input');
    const statusEl = document.getElementById('status');
    const logEl = document.getElementById('log');
    const downloadBtn = document.getElementById('download-btn');
    const cookiesStatus = document.getElementById('cookie-status');
    const cookiesFileInput = document.getElementById('cookies-file');
    const uploadCookiesBtn = document.getElementById('upload-cookies-btn');
    const downloadsEl = document.getElementById('downloads');

    const setStatus = (message, isError = false) => {
        statusEl.textContent = message;
        statusEl.style.color = isError ? '#fca5a5' : '#cbd5e1';
    };

    const appendLog = (lines) => {
        logEl.textContent = lines.length ? lines.join('\n') : 'No output yet.';
    };

    const progressRow = document.getElementById('progress-row');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const phaseText = document.getElementById('phase');
    const lastUpdateText = document.getElementById('last-update');
    const spinner = document.getElementById('spinner');

    let eventSource = null;
    let currentJobId = null;
    let logs = [];
    let stallTimer = null;
    let lastTimestamp = null;

    const renderDownloads = (items = []) => {
        if (!downloadsEl) return;

        downloadsEl.innerHTML = '';

        if (!items.length) {
            downloadsEl.textContent = 'No downloads yet.';
            return;
        }

        items.forEach((item) => {
            const row = document.createElement('div');
            row.className = 'download-row';

            const link = document.createElement('a');
            link.href = item.url;
            link.textContent = item.filename || 'Download mp3';
            link.setAttribute('download', item.filename || 'audio.mp3');

            const meta = document.createElement('code');
            meta.textContent = item.filename || '';

            row.appendChild(link);
            if (item.filename) {
                row.appendChild(meta);
            }

            downloadsEl.appendChild(row);
        });
    };

    const refreshCookieStatus = async () => {
        if (!cookiesStatus) return;

        try {
            const response = await fetch('/api/youtube/cookies/status');
            const payload = await response.json();

            if (!response.ok) {
                throw new Error(payload?.detail || 'Unable to check cookies status');
            }

            const configured = payload?.configured;
            const source = payload?.source;
            const error = payload?.error;

            if (error) {
                cookiesStatus.textContent = `Cookies error: ${error}`;
                cookiesStatus.style.color = '#fca5a5';
                return;
            }

            cookiesStatus.style.color = '#cbd5e1';
            if (configured) {
                cookiesStatus.textContent = `Cookies loaded (${source || 'provided'}).`;
            } else {
                cookiesStatus.textContent = 'No cookies configured. Upload cookies.txt if downloads need authentication.';
            }
        } catch (err) {
            console.error(err);
            cookiesStatus.textContent = 'Unable to determine cookies status.';
            cookiesStatus.style.color = '#fca5a5';
        }
    };

    const uploadCookies = async (file) => {
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);

        uploadCookiesBtn.disabled = true;
        cookiesStatus.textContent = 'Uploading cookies...';
        cookiesStatus.style.color = '#cbd5e1';

        try {
            const response = await fetch('/api/youtube/cookies', {
                method: 'POST',
                body: formData,
            });

            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload?.detail || response.statusText);
            }

            cookiesStatus.textContent = 'Cookies uploaded and active for new downloads.';
            cookiesStatus.style.color = '#cbd5e1';
        } catch (err) {
            console.error(err);
            cookiesStatus.textContent = `Cookies upload failed: ${err.message}`;
            cookiesStatus.style.color = '#fca5a5';
        } finally {
            uploadCookiesBtn.disabled = false;
            cookiesFileInput.value = '';
        }
    };

    const setProgress = (payload = {}) => {
        if (!progressRow) return;
        const percent = Number(payload.percent ?? 0);
        const percentDisplay = Number.isFinite(percent) ? Math.min(Math.max(percent, 0), 100).toFixed(1) : '…';
        if (progressBar) progressBar.style.width = `${Number.isFinite(percent) ? percent : 0}%`;

        const downloaded = payload.downloaded_bytes;
        const total = payload.total_bytes;
        const speed = payload.speed || '';
        const eta = payload.eta || '';

        const sizeText = downloaded && total
            ? `${(downloaded / 1024 / 1024).toFixed(1)}MB / ${(total / 1024 / 1024).toFixed(1)}MB`
            : '';

        progressText.textContent = `${percentDisplay}% ${sizeText ? `— ${sizeText}` : ''} ${speed ? `— ${speed}` : ''} ${eta ? `— ETA ${eta}` : ''}`.trim();
    };

    const setPhase = (phase) => {
        if (phaseText) {
            phaseText.textContent = phase || 'Working...';
        }
    };

    const setLastUpdate = (ts) => {
        if (!lastUpdateText) return;
        if (ts) lastTimestamp = ts;
        const target = ts || lastTimestamp || Date.now() / 1000;
        const now = new Date(target * 1000);
        lastUpdateText.textContent = `Last update: ${now.toLocaleTimeString()}`;
    };

    const startStallWatcher = () => {
        clearInterval(stallTimer);
        stallTimer = setInterval(() => {
            if (!lastTimestamp) return;
            const seconds = Date.now() / 1000 - lastTimestamp;
            if (seconds > 20) {
                setStatus('No updates for >20s — download may still be running, please wait...', false);
            }
        }, 5000);
    };

    const resetUI = () => {
        logs = [];
        appendLog([]);
        renderDownloads([]);
        if (downloadsEl) downloadsEl.textContent = 'Awaiting download...';
        if (progressRow) progressRow.style.display = 'none';
        if (progressBar) progressBar.style.width = '0%';
        if (progressText) progressText.textContent = '0% — pending';
        if (phaseText) phaseText.textContent = 'Awaiting start...';
        if (lastUpdateText) lastUpdateText.textContent = '';
        lastTimestamp = null;
    };

    const openStream = (jobId) => {
        if (!jobId) return;
        currentJobId = jobId;
        if (eventSource) eventSource.close();

        progressRow.style.display = 'flex';
        setStatus('Waiting for progress...');
        spinner.style.display = 'block';
        startStallWatcher();

        eventSource = new EventSource(`/api/youtube/jobs/${jobId}/events`);

        eventSource.addEventListener('state', (evt) => {
            const payload = JSON.parse(evt.data || '{}');
            if (Array.isArray(payload.logs)) {
                logs = payload.logs;
                appendLog(logs);
            }
            if (payload.downloads) renderDownloads(payload.downloads);
            if (payload.status === 'completed') {
                setStatus('Finished. Download ready below.');
                spinner.style.display = 'none';
            } else if (payload.status === 'error') {
                setStatus('Download failed. See logs for details.', true);
                spinner.style.display = 'none';
            }
            if (payload.phase) setPhase(payload.phase);
            if (payload.progress) setProgress(payload.progress);
            if (payload.last_update) setLastUpdate(payload.last_update);
        });

        eventSource.addEventListener('log', (evt) => {
            const payload = JSON.parse(evt.data || '{}');
            if (payload.line) {
                logs.push(payload.line);
                appendLog(logs);
            }
            if (payload.timestamp) setLastUpdate(payload.timestamp);
        });

        eventSource.addEventListener('progress', (evt) => {
            const payload = JSON.parse(evt.data || '{}');
            setProgress(payload);
            if (payload.timestamp) setLastUpdate(payload.timestamp);
            setStatus('Downloading...');
        });

        eventSource.addEventListener('downloads', (evt) => {
            const payload = JSON.parse(evt.data || '[]');
            renderDownloads(payload);
        });

        eventSource.addEventListener('heartbeat', (evt) => {
            const payload = JSON.parse(evt.data || '{}');
            if (payload.timestamp) setLastUpdate(payload.timestamp);
        });

        eventSource.addEventListener('finished', (evt) => {
            const payload = JSON.parse(evt.data || '{}');
            if (payload.status === 'completed') {
                setStatus('Finished. Download ready below.');
            } else {
                setStatus('Download failed. Check logs.', true);
            }
            spinner.style.display = 'none';
            if (payload.downloads) renderDownloads(payload.downloads);
            if (payload.logs) appendLog(payload.logs);
            if (eventSource) eventSource.close();
            clearInterval(stallTimer);
        });

        eventSource.onerror = () => {
            setStatus('Connection lost. Attempting to reconnect...', true);
        };
    };

    const startDownload = async () => {
        const urls = urlInput.value.trim();
        if (!urls) {
            alert('Please enter at least one URL.');
            return;
        }

        downloadBtn.disabled = true;
        setStatus('Starting download...');
        resetUI();
        if (eventSource) eventSource.close();

        try {
            const response = await fetch('/api/youtube/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ urls }),
            });

            const payload = await response.json();
            if (!response.ok) {
                const detail = payload?.detail || response.statusText;
                throw new Error(detail);
            }

            openStream(payload.job_id);
        } catch (err) {
            console.error(err);
            setStatus(`Failed: ${err.message}`, true);
        } finally {
            downloadBtn.disabled = false;
        }
    };

    downloadBtn?.addEventListener('click', startDownload);

    uploadCookiesBtn?.addEventListener('click', () => cookiesFileInput?.click());
    cookiesFileInput?.addEventListener('change', () => uploadCookies(cookiesFileInput.files[0]));

    refreshCookieStatus();
    renderDownloads();
})();
