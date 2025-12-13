(() => {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const pickBtn = document.getElementById('pick-btn');
    const clearBtn = document.getElementById('clear-btn');
    const uploadBtn = document.getElementById('upload-btn');
    const statusEl = document.getElementById('status');
    const fileListEl = document.getElementById('file-list');
    const logEl = document.getElementById('log');

    const config = (window.PAYSLIP_AUDIT_CONFIG || {});
    const uploadEndpoint = config.uploadEndpoint || '/api/payslip-audit/run';
    const reportBase = config.reportBase || '/api/payslip-audit/report/';

    let selectedFiles = [];
    let uploadInFlight = false;

    const setStatus = (message, isError = false) => {
        statusEl.textContent = message;
        statusEl.style.color = isError ? '#fca5a5' : '#cbd5e1';
    };

    const setLog = (message) => {
        logEl.textContent = message || '';
    };

    const renderFileList = () => {
        if (!fileListEl) return;
        fileListEl.innerHTML = '';
        selectedFiles.forEach((file) => {
            const item = document.createElement('li');
            item.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
            fileListEl.appendChild(item);
        });
    };

    const addFiles = (files) => {
        const unique = new Map();
        selectedFiles.forEach((file) => unique.set(file.name, file));
        Array.from(files).forEach((file) => unique.set(file.name, file));
        selectedFiles = Array.from(unique.values());
        renderFileList();
        setStatus('Files ready. Click "Upload & Start Audit" to proceed.');
    };

    const reset = () => {
        selectedFiles = [];
        renderFileList();
        setStatus('Select your payslip PDF and timesheet screenshots to begin.');
        setLog('Awaiting upload...');
        if (fileInput) fileInput.value = '';
    };

    const triggerDownload = (url) => {
        const link = document.createElement('a');
        link.href = url;
        link.download = 'audit_report.pdf';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    const uploadAndRun = async () => {
        if (uploadInFlight) return;
        if (!selectedFiles.length) {
            setStatus('Please add your payslip PDF and timesheet images before starting.', true);
            return;
        }

        uploadInFlight = true;
        setStatus('Uploading files and starting audit...');
        setLog('Working...');

        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append('files', file));

        try {
            const response = await fetch(uploadEndpoint, { method: 'POST', body: formData });
            const payload = await response.json();
            if (!response.ok) {
                const detail = payload && payload.detail ? payload.detail : response.statusText;
                throw new Error(detail);
            }

            setStatus('Audit finished. Downloading report...');
            setLog(payload.log || 'Audit completed successfully.');

            const downloadUrl = payload.download_url || (payload.session_id ? `${reportBase}${payload.session_id}` : null);
            if (downloadUrl) {
                triggerDownload(downloadUrl);
            }
        } catch (err) {
            console.error(err);
            setStatus(`Audit failed: ${err.message || err}`, true);
            setLog('Double-check that you uploaded one payslip PDF and at least one JPG/PNG timesheet.');
        } finally {
            uploadInFlight = false;
        }
    };

    const handleDrop = (event) => {
        event.preventDefault();
        dropZone.classList.remove('dragover');
        if (event.dataTransfer?.files?.length) {
            addFiles(event.dataTransfer.files);
        }
    };

    dropZone?.addEventListener('dragover', (event) => {
        event.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone?.addEventListener('dragleave', (event) => {
        event.preventDefault();
        dropZone.classList.remove('dragover');
    });

    dropZone?.addEventListener('drop', handleDrop);

    pickBtn?.addEventListener('click', () => fileInput?.click());

    clearBtn?.addEventListener('click', reset);

    fileInput?.addEventListener('change', (event) => {
        const files = event.target?.files;
        if (files) {
            addFiles(files);
        }
    });

    uploadBtn?.addEventListener('click', uploadAndRun);

    setStatus('Select your payslip PDF and timesheet screenshots to begin.');
})();
