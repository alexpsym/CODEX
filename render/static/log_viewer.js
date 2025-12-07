(() => {
    const logBox = document.getElementById('log-box');
    const saveBtn = document.getElementById('save-log-btn');
    const refreshBtn = document.getElementById('refresh-btn');
    const lineCount = document.getElementById('line-count');
    const scriptName = (window.RENDER_LOG_VIEW && window.RENDER_LOG_VIEW.scriptName) || '';

    let cachedLines = [];
    let refreshTimer = null;

    const setLineCount = () => {
        const count = cachedLines.length;
        lineCount.textContent = `${count} ${count === 1 ? 'line' : 'lines'}`;
    };

    const fetchLogs = async () => {
        try {
            const response = await fetch(`/logs/${encodeURIComponent(scriptName)}`);
            if (!response.ok) {
                throw new Error(`Failed to load logs (${response.status})`);
            }
            cachedLines = await response.json();
            logBox.textContent = cachedLines.length ? cachedLines.join('\n') : 'No logs yet. Start the script to see output.';
            setLineCount();
        } catch (err) {
            console.error(err);
            logBox.textContent = 'Unable to load logs. Please retry or check the server.';
        }
    };

    const downloadLog = () => {
        const header = [
            'Render Master Control Log Export',
            `Script: ${scriptName}`,
            `Exported: ${new Date().toISOString()}`,
            `Total lines: ${cachedLines.length}`,
            '----------------------------------------',
        ];
        const body = cachedLines.length ? cachedLines : ['No log output was available at export time.'];
        const content = [...header, ...body].join('\n');
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        link.download = `${scriptName || 'render-script'}-log-${timestamp}.txt`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    };

    saveBtn?.addEventListener('click', downloadLog);
    refreshBtn?.addEventListener('click', fetchLogs);

    refreshTimer = setInterval(fetchLogs, 3000);
    fetchLogs();

    window.addEventListener('beforeunload', () => {
        if (refreshTimer) {
            clearInterval(refreshTimer);
        }
    });
})();
