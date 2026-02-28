document.getElementById('options-link').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
});

chrome.storage.local.get(['gcdApiBase', 'gcdApiKey'], (data) => {
    const statusEl = document.getElementById('status');
    const dot = document.getElementById('dot');

    if (!data.gcdApiKey) {
        statusEl.textContent = 'API key not set — open Settings to configure.';
        statusEl.className = 'status warn';
        dot.style.background = '#f59e0b';
        return;
    }

    const base = (data.gcdApiBase || 'https://codriverfreight.com').replace(/\/$/, '');
    fetch(`${base}/api/scout/parsing-rules`, {
        headers: { 'x-api-key': data.gcdApiKey },
        signal: AbortSignal.timeout(5000),
    })
        .then((r) => {
            if (r.ok) {
                statusEl.textContent = 'Connected — Scout is active.';
                statusEl.className = 'status ok';
            } else {
                statusEl.textContent = `Not connected (${r.status}) — check API key.`;
                statusEl.className = 'status warn';
                dot.style.background = '#ef4444';
            }
        })
        .catch(() => {
            statusEl.textContent = 'Cannot reach CoDriver server.';
            statusEl.className = 'status warn';
            dot.style.background = '#ef4444';
        });
});
