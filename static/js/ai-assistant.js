(function () {
    'use strict';

    const state = {
        initialized: false,
        user: null,
        enabled: false,
        fixedServerId: null,
        conversationId: null,
        runId: null,
        socket: null,
        reconnectTimer: null,
        pollTimer: null,
        lastSequence: 0,
    };

    const element = (id) => document.getElementById(id);
    const translate = (key, fallback) => window.i18n?.t(key) || fallback;

    async function jsonResponse(response) {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = Array.isArray(data.detail)
                ? data.detail.map((item) => item.msg).join('; ')
                : data.detail;
            throw new Error(detail || `HTTP ${response.status}`);
        }
        return data;
    }

    function setStatus(message, isError = false) {
        const target = element('ai-run-status');
        if (!target) return;
        target.textContent = message;
        target.classList.remove('d-none', 'text-danger', 'text-muted');
        target.classList.add(isError ? 'text-danger' : 'text-muted');
    }

    function clearStatus() {
        element('ai-run-status')?.classList.add('d-none');
    }

    function appendMessage(role, content) {
        if (!content) return;
        const list = element('ai-message-list');
        const item = document.createElement('div');
        item.className = `ai-message ai-message-${role}`;
        item.textContent = content;
        list.appendChild(item);
        list.scrollTop = list.scrollHeight;
    }

    function appendToolCard(tool) {
        if (element(`ai-tool-${tool.id || tool.tool_run_id}`)) return;
        const id = tool.id || tool.tool_run_id;
        const card = document.createElement('div');
        card.id = `ai-tool-${id}`;
        card.className = 'card ai-tool-card';

        const body = document.createElement('div');
        body.className = 'card-body p-2';
        const title = document.createElement('div');
        title.className = 'fw-semibold small mb-1';
        title.textContent = `${translate('ai.approvalRequired', 'Approval required')}: ${tool.tool_name}`;
        const pre = document.createElement('pre');
        pre.className = 'ai-tool-arguments bg-body-tertiary rounded p-2 mb-2';
        pre.textContent = JSON.stringify({
            summary: tool.summary || null,
            arguments: tool.arguments || {},
        }, null, 2);
        const controls = document.createElement('div');
        controls.className = 'd-flex gap-2';
        const approve = document.createElement('button');
        approve.type = 'button';
        approve.className = 'btn btn-sm btn-warning';
        approve.textContent = translate('ai.approve', 'Approve');
        const reject = document.createElement('button');
        reject.type = 'button';
        reject.className = 'btn btn-sm btn-outline-secondary';
        reject.textContent = translate('ai.reject', 'Reject');
        approve.addEventListener('click', () => decideTool(id, tool.arguments_hash, 'approve', card));
        reject.addEventListener('click', () => decideTool(id, tool.arguments_hash, 'reject', card));
        controls.append(approve, reject);
        body.append(title, pre, controls);
        card.appendChild(body);
        element('ai-message-list').appendChild(card);
        element('ai-message-list').scrollTop = element('ai-message-list').scrollHeight;
    }

    async function decideTool(toolId, argumentsHash, decision, card) {
        card.querySelectorAll('button').forEach((button) => { button.disabled = true; });
        try {
            const response = await authFetch(`/api/ai/runs/${state.runId}/tools/${toolId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, arguments_hash: argumentsHash }),
            });
            await jsonResponse(response);
            card.querySelector('.fw-semibold').textContent =
                decision === 'approve'
                    ? translate('ai.approved', 'Approved')
                    : translate('ai.rejected', 'Rejected');
            card.querySelector('.d-flex')?.remove();
            setStatus(translate('ai.running', 'AI task is running…'));
            pollRun();
        } catch (error) {
            setStatus(error.message, true);
            card.querySelectorAll('button').forEach((button) => { button.disabled = false; });
        }
    }

    async function loadProviderStatus() {
        try {
            const response = await authFetch('/api/auth/ai-settings');
            const settings = await jsonResponse(response);
            state.enabled = settings.effective_enabled;
            const label = element('ai-provider-status');
            label.textContent = settings.effective_enabled
                ? `${translate('ai.providerReady', 'Provider ready')} · ${settings.effective_source}`
                : translate('ai.providerUnavailable', 'AI provider is disabled or untested');
            label.className = settings.effective_enabled ? 'text-success' : 'text-danger';
            element('ai-message-input').disabled = !settings.effective_enabled;
            element('ai-send-button').disabled = !settings.effective_enabled;
        } catch (error) {
            state.enabled = false;
            element('ai-provider-status').textContent = error.message;
            element('ai-provider-status').className = 'text-danger';
        }
    }

    async function loadServers() {
        const match = window.location.pathname.match(/^\/servers\/(\d+)(?:\/|$)/);
        state.fixedServerId = match ? Number(match[1]) : null;
        const endpoint = state.user.is_admin ? '/servers/admin/all?limit=100' : '/servers?limit=100';
        const servers = await jsonResponse(await authFetch(endpoint));
        const select = element('ai-server-select');
        select.replaceChildren();
        const none = document.createElement('option');
        none.value = '';
        none.textContent = translate('ai.noServer', 'No server selected');
        select.appendChild(none);
        servers.forEach((server) => {
            const option = document.createElement('option');
            option.value = String(server.id);
            option.textContent = server.name;
            select.appendChild(option);
        });
        if (state.fixedServerId) {
            select.value = String(state.fixedServerId);
            select.disabled = true;
        }
    }

    async function loadConversations(preferredId = null) {
        const conversations = await jsonResponse(await authFetch('/api/ai/conversations'));
        const select = element('ai-conversation-select');
        select.replaceChildren();
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = translate('ai.history', 'Conversation history');
        select.appendChild(placeholder);
        conversations.forEach((conversation) => {
            const option = document.createElement('option');
            option.value = conversation.id;
            option.textContent = conversation.title;
            option.dataset.serverId = conversation.server_id ?? '';
            select.appendChild(option);
        });
        const target = preferredId || state.conversationId;
        if (target && conversations.some((item) => item.id === target)) {
            select.value = target;
            await openConversation(target);
        }
    }

    async function openConversation(conversationId) {
        stopRunWatch();
        const conversation = await jsonResponse(
            await authFetch(`/api/ai/conversations/${conversationId}`)
        );
        state.conversationId = conversation.id;
        element('ai-conversation-select').value = conversation.id;
        if (!state.fixedServerId) {
            element('ai-server-select').value = conversation.server_id ?? '';
        }
        const list = element('ai-message-list');
        list.replaceChildren();
        conversation.messages.forEach((message) => appendMessage(message.role, message.content));
        clearStatus();
    }

    function newConversation() {
        stopRunWatch();
        state.conversationId = null;
        element('ai-conversation-select').value = '';
        element('ai-message-list').replaceChildren();
        clearStatus();
    }

    async function ensureConversation() {
        if (state.conversationId) return state.conversationId;
        const serverValue = element('ai-server-select').value;
        const response = await authFetch('/api/ai/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_id: serverValue ? Number(serverValue) : null }),
        });
        const conversation = await jsonResponse(response);
        state.conversationId = conversation.id;
        await loadConversations(conversation.id);
        return conversation.id;
    }

    async function sendMessage(event) {
        event.preventDefault();
        if (!state.enabled || state.runId) return;
        const input = element('ai-message-input');
        const content = input.value.trim();
        if (!content) return;
        element('ai-send-button').disabled = true;
        try {
            const conversationId = await ensureConversation();
            appendMessage('user', content);
            input.value = '';
            const response = await authFetch(`/api/ai/conversations/${conversationId}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content }),
            });
            const run = await jsonResponse(response);
            state.runId = run.id;
            state.lastSequence = 0;
            setStatus(translate('ai.running', 'AI task is running…'));
            connectEvents();
            pollRun();
        } catch (error) {
            setStatus(error.message, true);
            element('ai-send-button').disabled = !state.enabled;
        }
    }

    function connectEvents() {
        if (!state.runId) return;
        state.socket?.close();
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(
            `${protocol}//${window.location.host}/api/ai/runs/${state.runId}/events?after=${state.lastSequence}`
        );
        state.socket = socket;
        socket.addEventListener('message', (event) => {
            const message = JSON.parse(event.data);
            state.lastSequence = Math.max(state.lastSequence, Number(message.sequence || 0));
            handleRunEvent(message);
        });
        socket.addEventListener('close', () => {
            if (state.runId) {
                clearTimeout(state.reconnectTimer);
                state.reconnectTimer = setTimeout(connectEvents, 1500);
            }
        });
    }

    function handleRunEvent(event) {
        const payload = event.payload || {};
        if (event.type === 'assistant_message') appendMessage('assistant', payload.content);
        if (event.type === 'tool_approval_required') appendToolCard(payload);
        if (event.type === 'tool_started') {
            setStatus(`${translate('ai.runningTool', 'Running tool')}: ${payload.tool_name}`);
        }
        if (event.type === 'tool_progress' && payload.message) setStatus(payload.message);
        if (event.type === 'run_waiting_approval') {
            setStatus(translate('ai.waitingApproval', 'Waiting for your approval'));
        }
        if (event.type === 'run_failed') finishRun(payload.error, true);
        if (event.type === 'run_completed') finishRun(translate('ai.completed', 'Completed'));
    }

    async function pollRun() {
        clearTimeout(state.pollTimer);
        if (!state.runId) return;
        try {
            const run = await jsonResponse(await authFetch(`/api/ai/runs/${state.runId}`));
            run.tools
                .filter((tool) => tool.status === 'pending_approval')
                .forEach(appendToolCard);
            if (['completed', 'failed', 'interrupted'].includes(run.status)) {
                finishRun(run.error || translate('ai.completed', 'Completed'), run.status !== 'completed');
                return;
            }
        } catch (error) {
            setStatus(error.message, true);
        }
        state.pollTimer = setTimeout(pollRun, 2000);
    }

    async function finishRun(message, isError = false) {
        const conversationId = state.conversationId;
        stopRunWatch();
        setStatus(message, isError);
        element('ai-send-button').disabled = !state.enabled;
        if (conversationId) {
            try {
                await openConversation(conversationId);
                await loadConversations(conversationId);
            } catch (error) {
                setStatus(error.message, true);
            }
        }
    }

    function stopRunWatch() {
        state.runId = null;
        clearTimeout(state.reconnectTimer);
        clearTimeout(state.pollTimer);
        if (state.socket) {
            state.socket.onclose = null;
            state.socket.close();
            state.socket = null;
        }
    }

    async function initialize(user) {
        if (!user || state.initialized) return;
        state.initialized = true;
        state.user = user;
        element('ai-assistant-toggle').classList.remove('d-none');
        try {
            await Promise.all([loadProviderStatus(), loadServers()]);
            await loadConversations();
        } catch (error) {
            setStatus(error.message, true);
        }
    }

    function bind() {
        element('ai-message-form')?.addEventListener('submit', sendMessage);
        element('ai-new-conversation')?.addEventListener('click', newConversation);
        element('ai-conversation-select')?.addEventListener('change', (event) => {
            if (event.target.value) openConversation(event.target.value).catch((error) => setStatus(error.message, true));
            else newConversation();
        });
        element('ai-server-select')?.addEventListener('change', newConversation);
        element('ai-example-prompts')?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-ai-prompt]');
            if (!button) return;
            const input = element('ai-message-input');
            input.value = translate(button.dataset.aiPrompt, button.textContent.trim());
            input.focus();
        });
        window.addEventListener('authReady', (event) => initialize(event.detail.user));
        if (localStorage.getItem('access_token')) {
            fetch('/api/auth/me', { headers: getAuthHeaders() })
                .then((response) => response.ok ? response.json() : null)
                .then(initialize)
                .catch(() => {});
        }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();
})();
