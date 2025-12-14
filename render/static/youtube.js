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

    const startDownload = async () => {
        const urls = urlInput.value.trim();
        if (!urls) {
            alert('Please enter at least one URL.');
            return;
        }

        downloadBtn.disabled = true;
        setStatus('Starting download...');
        appendLog([]);
        renderDownloads([]);
        if (downloadsEl) {
            downloadsEl.textContent = 'Awaiting download...';
        }

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

            appendLog(payload.log || []);
            renderDownloads(payload.downloads || []);
            setStatus('Finished. Check the log for details.');
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
