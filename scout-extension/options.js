document.getElementById('save').addEventListener('click', () => {
    const apiBase = document.getElementById('apiBase').value;
    const apiKey = document.getElementById('apiKey').value;

    chrome.storage.local.set(
        {
            gcdApiBase: apiBase,
            gcdApiKey: apiKey,
        },
        () => {
            const status = document.getElementById('status');
            status.textContent = 'Settings saved!';
            status.style.color = '#10b981';
            setTimeout(() => {
                status.textContent = '';
            }, 2000);
        }
    );
});

document.getElementById('test').addEventListener('click', async () => {
    const apiBase = (document.getElementById('apiBase').value || '').trim();
    const apiKey = (document.getElementById('apiKey').value || '').trim();
    const status = document.getElementById('status');

    if (!apiBase) {
        status.textContent = 'Enter API Base URL first.';
        status.style.color = '#f59e0b';
        return;
    }

    if (!apiKey) {
        status.textContent = 'Enter Scout API Key first.';
        status.style.color = '#f59e0b';
        return;
    }

    status.textContent = 'Testing connection...';
    status.style.color = '#60a5fa';

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);

    try {
        const response = await fetch(`${apiBase.replace(/\/$/, '')}/api/scout/parsing-rules`, {
            method: 'GET',
            headers: {
                'x-api-key': apiKey,
            },
            signal: controller.signal,
        });

        if (response.ok) {
            status.textContent = 'Connection OK ✓';
            status.style.color = '#10b981';
        } else if (response.status === 401) {
            status.textContent = 'Unauthorized: API key rejected';
            status.style.color = '#ef4444';
        } else {
            status.textContent = `Connection failed (${response.status})`;
            status.style.color = '#ef4444';
        }
    } catch (error) {
        status.textContent = 'Connection failed (network/timeout)';
        status.style.color = '#ef4444';
    } finally {
        clearTimeout(timeout);
        setTimeout(() => {
            if (status.textContent.startsWith('Connection OK')) {
                status.textContent = '';
            }
        }, 3000);
    }
});

chrome.storage.local.get(['gcdApiBase', 'gcdApiKey'], (data) => {
    if (data.gcdApiBase) document.getElementById('apiBase').value = data.gcdApiBase;
    if (data.gcdApiKey) document.getElementById('apiKey').value = data.gcdApiKey;
});
