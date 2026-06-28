(function () {
    const authToken = localStorage.getItem('auth_token') || '';
    if (!authToken) {
        window.location.href = '/';
        return;
    }

    const state = {
        selectedRequestId: '',
        requestItems: [],
    };

    const elements = {
        portInput: document.getElementById('portInput'),
        startUrlInput: document.getElementById('startUrlInput'),
        profileDirInput: document.getElementById('profileDirInput'),
        keywordInput: document.getElementById('keywordInput'),
        targetSelect: document.getElementById('targetSelect'),
        requestList: document.getElementById('requestList'),
        requestCount: document.getElementById('requestCount'),
        captureBadge: document.getElementById('captureBadge'),
        statusBox: document.getElementById('statusBox'),
        codeBox: document.getElementById('codeBox'),
        outputPathInput: document.getElementById('outputPathInput'),
        includeSensitiveInput: document.getElementById('includeSensitiveInput'),
        launchBtn: document.getElementById('launchBtn'),
        targetsBtn: document.getElementById('targetsBtn'),
        connectBtn: document.getElementById('connectBtn'),
        startBtn: document.getElementById('startBtn'),
        stopBtn: document.getElementById('stopBtn'),
        clearBtn: document.getElementById('clearBtn'),
        saveBtn: document.getElementById('saveBtn'),
    };

    function getPort() {
        return Number(elements.portInput.value || 9222);
    }

    function getSelectedResourceTypes() {
        return Array.from(document.querySelectorAll('.resourceType:checked')).map((item) => item.value);
    }

    async function api(path, options = {}) {
        const config = Object.assign({ method: 'GET', headers: {} }, options);
        config.headers = Object.assign({}, config.headers, {
            Authorization: `Bearer ${authToken}`,
        });
        if (config.body && !config.headers['Content-Type']) {
            config.headers['Content-Type'] = 'application/json';
        }
        const response = await fetch(path, config);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || data.message || `HTTP ${response.status}`);
        }
        if (data.status === 'error') {
            throw new Error(data.message || '请求失败');
        }
        return data;
    }

    function setStatus(text) {
        elements.statusBox.textContent = text;
    }

    function setBadge(stateText, type) {
        elements.captureBadge.textContent = stateText;
        elements.captureBadge.className = type;
    }

    function renderTargets(items) {
        const options = items.map((item) => {
            const option = document.createElement('option');
            option.value = item.id;
            option.textContent = `${item.title} | ${item.url || '(blank)'}`;
            option.dataset.websocketUrl = item.websocket_url;
            return option;
        });
        elements.targetSelect.innerHTML = '';
        if (!options.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '没有可连接页面';
            elements.targetSelect.appendChild(option);
            return;
        }
        options.forEach((option) => elements.targetSelect.appendChild(option));
    }

    function renderRequests(items) {
        state.requestItems = items;
        elements.requestCount.textContent = `${items.length} 条`;
        elements.requestList.innerHTML = '';

        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'request-item';
            empty.textContent = '暂无请求，开始监听后在 Edge 中继续操作。';
            elements.requestList.appendChild(empty);
            return;
        }

        items.forEach((item) => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = `request-item${state.selectedRequestId === item.id ? ' active' : ''}`;
            card.innerHTML = `
                <div class="request-topline">
                    <span class="method-tag">${item.method}</span>
                    <span class="type-tag">${item.resource_type}</span>
                </div>
                <p class="request-url">${item.url}</p>
                <div class="request-meta">${item.captured_at}${item.has_body ? ' | 含请求体' : ''}</div>
            `;
            card.addEventListener('click', () => {
                state.selectedRequestId = item.id;
                renderRequests(state.requestItems);
                loadCode(item.id);
            });
            elements.requestList.appendChild(card);
        });
    }

    async function refreshState() {
        try {
            const result = await api('/api/browser_monitor/state');
            const data = result.data || {};
            if (data.capture_running) {
                setBadge('监听中', 'status-badge status-running');
            } else if (data.capture_error) {
                setBadge('已停止', 'status-badge status-error');
            } else {
                setBadge('未监听', 'status-badge status-idle');
            }
            setStatus(JSON.stringify(data, null, 2));
        } catch (error) {
            setBadge('连接失败', 'status-badge status-error');
            setStatus(error.message || String(error));
        }
    }

    async function refreshTargets() {
        const result = await api(`/api/browser_monitor/targets?port=${encodeURIComponent(getPort())}`);
        renderTargets(result.data || []);
    }

    async function loadRequests() {
        const keyword = elements.keywordInput.value.trim();
        const query = new URLSearchParams({ limit: '80' });
        if (keyword) query.set('url_keyword', keyword);
        const result = await api(`/api/browser_monitor/requests?${query.toString()}`);
        renderRequests(result.data || []);
    }

    function getSelectedTargetPayload() {
        const selected = elements.targetSelect.options[elements.targetSelect.selectedIndex];
        return {
            port: getPort(),
            target_id: selected ? selected.value : '',
            target_ws_url: selected ? selected.dataset.websocketUrl || '' : '',
        };
    }

    async function launchEdge() {
        const payload = {
            port: getPort(),
            start_url: elements.startUrlInput.value.trim(),
            user_data_dir: elements.profileDirInput.value.trim(),
            reuse_existing: true,
        };
        const result = await api('/api/browser_monitor/launch_edge', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        setStatus(result.message || 'Edge 已启动');
        await refreshTargets();
        await refreshState();
    }

    async function connectTarget() {
        const result = await api('/api/browser_monitor/connect', {
            method: 'POST',
            body: JSON.stringify(getSelectedTargetPayload()),
        });
        setStatus(JSON.stringify(result.target || {}, null, 2));
        await refreshState();
    }

    async function startCapture() {
        const payload = Object.assign(getSelectedTargetPayload(), {
            resource_types: getSelectedResourceTypes(),
            url_keyword: elements.keywordInput.value.trim(),
            clear_existing: false,
        });
        const result = await api('/api/browser_monitor/start', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        setStatus(result.message || '已开始监听');
        await refreshState();
        await loadRequests();
    }

    async function stopCapture() {
        const result = await api('/api/browser_monitor/stop', { method: 'POST' });
        setStatus(result.message || '已停止监听');
        await refreshState();
    }

    async function clearRequests() {
        const result = await api('/api/browser_monitor/clear', { method: 'POST' });
        state.selectedRequestId = '';
        elements.codeBox.textContent = '选择一条请求后，这里会显示 Python requests 代码。';
        setStatus(result.message || '已清空');
        await loadRequests();
        await refreshState();
    }

    async function loadCode(requestId) {
        if (!requestId) return;
        const query = new URLSearchParams({
            request_id: requestId,
            client: 'requests',
            include_sensitive: elements.includeSensitiveInput.checked ? 'true' : 'false',
        });
        const result = await api(`/api/browser_monitor/request_code?${query.toString()}`);
        elements.codeBox.textContent = result.data.code || '';
    }

    async function saveCode() {
        if (!state.selectedRequestId) {
            throw new Error('请先选择一条请求。');
        }
        const payload = {
            request_id: state.selectedRequestId,
            output_path: elements.outputPathInput.value.trim(),
            client: 'requests',
            include_sensitive: elements.includeSensitiveInput.checked,
        };
        const result = await api('/api/browser_monitor/save_code', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        setStatus(`${result.message}\n${result.path}`);
    }

    elements.launchBtn.addEventListener('click', () => launchEdge().catch((error) => setStatus(error.message || String(error))));
    elements.targetsBtn.addEventListener('click', () => refreshTargets().catch((error) => setStatus(error.message || String(error))));
    elements.connectBtn.addEventListener('click', () => connectTarget().catch((error) => setStatus(error.message || String(error))));
    elements.startBtn.addEventListener('click', () => startCapture().catch((error) => setStatus(error.message || String(error))));
    elements.stopBtn.addEventListener('click', () => stopCapture().catch((error) => setStatus(error.message || String(error))));
    elements.clearBtn.addEventListener('click', () => clearRequests().catch((error) => setStatus(error.message || String(error))));
    elements.saveBtn.addEventListener('click', () => saveCode().catch((error) => setStatus(error.message || String(error))));
    elements.includeSensitiveInput.addEventListener('change', () => {
        if (state.selectedRequestId) {
            loadCode(state.selectedRequestId).catch((error) => setStatus(error.message || String(error)));
        }
    });

    async function bootstrap() {
        await refreshState();
        try {
            await refreshTargets();
        } catch (error) {
            setStatus(error.message || String(error));
        }
        try {
            await loadRequests();
        } catch (error) {
            setStatus(error.message || String(error));
        }
        setInterval(() => {
            refreshState();
            loadRequests();
        }, 2500);
    }

    bootstrap();
})();
