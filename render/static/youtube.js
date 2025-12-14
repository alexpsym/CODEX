(() => {
    const urlInput = document.getElementById('url-input');
    const statusEl = document.getElementById('status');
    const logEl = document.getElementById('log');
    const downloadBtn = document.getElementById('download-btn');

    const setStatus = (message, isError = false) => {
        statusEl.textContent = message;
        statusEl.style.color = isError ? '#fca5a5' : '#cbd5e1';
    };

    const appendLog = (lines) => {
        logEl.textContent = lines.length ? lines.join('\n') : 'No output yet.';
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
            setStatus('Finished. Check the log for details.');
        } catch (err) {
            console.error(err);
            setStatus(`Failed: ${err.message}`, true);
        } finally {
            downloadBtn.disabled = false;
        }
    };

    downloadBtn?.addEventListener('click', startDownload);
})();
