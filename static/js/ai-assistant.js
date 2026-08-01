(function () {
    'use strict';

    const state = {
        initialized: false,
        user: null,
        enabled: false,
        fixedServerId: null,
        conversationId: null,
        runId: null,
        eventAbortController: null,
        reconnectTimer: null,
        pollTimer: null,
        taskViewerTimer: null,
        loadingBackgroundTasks: false,
        lastSequence: '0',
        streamedMessages: new Map(),
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
        target.innerHTML = '';
        const span = document.createElement('span');
        span.textContent = message;
        target.appendChild(span);
        const dots = document.createElement('span');
        dots.className = 'ai-status-dots';
        target.appendChild(dots);
        target.classList.remove('d-none', 'text-danger', 'text-muted');
        target.classList.remove('ai-status-active');
        if (isError) {
            target.classList.add('text-danger');
        } else {
            target.classList.add('text-muted', 'ai-status-active');
        }
    }

    function clearActiveStatus() {
        const target = element('ai-run-status');
        if (!target) return;
        target.classList.remove('ai-status-active');
        const dots = target.querySelector('.ai-status-dots');
        if (dots) dots.remove();
    }

    function clearStatus() {
        element('ai-run-status')?.classList.add('d-none');
    }

    function operationStatusLabel(status) {
        return translate(`ai.operation.${status}`, String(status || '').replaceAll('_', ' '));
    }

    function operationStatusClass(status) {
        const classes = {
            queued: 'text-bg-info',
            running: 'text-bg-primary',
            completed: 'text-bg-success',
            failed: 'text-bg-danger',
            waiting_approval: 'text-bg-warning',
            rejected: 'text-bg-secondary'
        };
        return classes[status] || 'text-bg-secondary';
    }

    function renderAssistantMarkdown(target, content) {
        target._aiMarkdownSource = content;
        if (!window.marked || !window.DOMPurify) {
            target.textContent = content;
            return;
        }
        const rendered = window.marked.parse(content, { gfm: true, breaks: true });
        target.innerHTML = window.DOMPurify.sanitize(rendered, {
            USE_PROFILES: { html: true },
            FORBID_TAGS: [
                'style', 'iframe', 'object', 'embed', 'form', 'input', 'button',
                'textarea', 'select', 'img', 'video', 'audio', 'source', 'svg', 'math'
            ],
            FORBID_ATTR: ['style', 'srcset'],
        });
        target.querySelectorAll('a').forEach((link) => {
            link.target = '_blank';
            link.rel = 'noopener noreferrer nofollow';
        });
    }

    function appendMessage(role, content, allowEmpty = false) {
        if (!content && !allowEmpty) return null;
        const list = element('ai-message-list');
        const item = document.createElement('div');
        item.className = `ai-message ai-message-${role}`;
        if (role === 'assistant') renderAssistantMarkdown(item, content || '');
        else item.textContent = content;
        list.appendChild(item);
        list.scrollTop = list.scrollHeight;
        return item;
    }

    function streamKey(payload) {
        return `${state.runId || 'run'}:${payload.round || 0}`;
    }

    function appendAssistantDelta(payload) {
        if (typeof payload.delta !== 'string' || !payload.delta) return;
        const key = streamKey(payload);
        let streamed = state.streamedMessages.get(key);
        if (!streamed) {
            const item = appendMessage('assistant', '', true);
            item.classList.add('ai-message-streaming');
            streamed = { item, content: '' };
            state.streamedMessages.set(key, streamed);
        }
        streamed.content += payload.delta;
        renderAssistantMarkdown(streamed.item, streamed.content);
        const list = element('ai-message-list');
        list.scrollTop = list.scrollHeight;
    }

    function finalizeAssistantMessage(payload) {
        const content = typeof payload.content === 'string' ? payload.content : '';
        const key = streamKey(payload);
        const streamed = state.streamedMessages.get(key);
        if (!streamed) {
            appendMessage('assistant', content);
            return;
        }
        streamed.content = content;
        renderAssistantMarkdown(streamed.item, content);
        streamed.item.classList.remove('ai-message-streaming');
        state.streamedMessages.delete(key);
    }

    function upsertToolStatus(payload, status, message = '') {
        const id = payload.tool_run_id || payload.id;
        if (!id) return;
        let card = element(`ai-operation-${id}`);
        if (!card) {
            card = document.createElement('div');
            card.id = `ai-operation-${id}`;
            card.className = 'card ai-operation-card mb-2';
            const body = document.createElement('div');
            body.className = 'card-body p-2';
            const header = document.createElement('div');
            header.className = 'd-flex justify-content-between gap-2 small';
            const name = document.createElement('span');
            name.className = 'fw-semibold ai-operation-name';
            name.textContent = payload.tool_name || translate('ai.runningTool', 'Running tool');
            const badge = document.createElement('span');
            badge.className = 'badge ai-operation-status';
            header.append(name, badge);
            const detail = document.createElement('div');
            detail.className = 'small text-muted mt-1 ai-operation-detail';
            body.append(header, detail);
            card.appendChild(body);
            element('ai-message-list').appendChild(card);
        }
        const badge = card.querySelector('.ai-operation-status');
        badge.className = `badge ai-operation-status ${operationStatusClass(status)}`;
        badge.textContent = operationStatusLabel(status);
        if (message) card.querySelector('.ai-operation-detail').textContent = message;
        card.classList.toggle('ai-operation-active', status === 'running' || status === 'queued');
        if (status === 'queued' || status === 'running') {
            badge.setAttribute('aria-live', 'polite');
        } else {
            badge.removeAttribute('aria-live');
        }
        const list = element('ai-message-list');
        list.scrollTop = list.scrollHeight;
    }

    function appendBackgroundTaskStatus(parent, status) {
        const badge = document.createElement('span');
        badge.className = `badge ${operationStatusClass(status)}`;
        badge.textContent = operationStatusLabel(status);
        parent.appendChild(badge);
    }

    function renderBackgroundTasks(tasks) {
        const list = element('ai-background-task-list');
        if (!list) return;
        list.replaceChildren();
        if (!tasks.length) {
            const empty = document.createElement('div');
            empty.className = 'small text-muted';
            empty.textContent = translate('ai.noBackgroundTasks', 'No AI tasks yet');
            list.appendChild(empty);
            return;
        }
        tasks.forEach((task) => {
            const card = document.createElement('div');
            card.className = 'ai-background-task small';
            const header = document.createElement('div');
            header.className = 'd-flex align-items-center justify-content-between gap-2';
            const label = document.createElement('span');
            label.className = 'fw-semibold text-truncate';
            label.textContent = task.tools?.map((tool) => tool.tool_name).join(', ') || task.id;
            header.appendChild(label);
            appendBackgroundTaskStatus(header, task.status);
            card.appendChild(header);

            const tools = document.createElement('div');
            tools.className = 'ai-background-task-tools mt-2';
            (task.tools || []).forEach((tool) => {
                const row = document.createElement('div');
                row.className = 'd-flex align-items-center justify-content-between gap-2 text-muted';
                const name = document.createElement('span');
                name.className = 'text-truncate';
                name.textContent = tool.tool_name;
                row.appendChild(name);
                appendBackgroundTaskStatus(row, tool.status);
                tools.appendChild(row);
                if (tool.error) {
                    const error = document.createElement('div');
                    error.className = 'text-danger';
                    error.textContent = tool.error;
                    tools.appendChild(error);
                }
            });
            if (tools.childElementCount) card.appendChild(tools);
            if (task.error) {
                const error = document.createElement('div');
                error.className = 'text-danger mt-2';
                error.textContent = task.error;
                card.appendChild(error);
            }
            list.appendChild(card);
        });
    }

    async function refreshBackgroundTasks() {
        if (state.loadingBackgroundTasks) return;
        state.loadingBackgroundTasks = true;
        try {
            const tasks = await jsonResponse(await authFetch('/api/ai/tasks'));
            renderBackgroundTasks(tasks);
        } catch (error) {
            const list = element('ai-background-task-list');
            if (list) {
                list.replaceChildren();
                const message = document.createElement('div');
                message.className = 'small text-danger';
                message.textContent = error.message;
                list.appendChild(message);
            }
        } finally {
            state.loadingBackgroundTasks = false;
        }
    }

    function scheduleBackgroundTaskRefresh() {
        clearTimeout(state.taskViewerTimer);
        state.taskViewerTimer = setTimeout(async () => {
            await refreshBackgroundTasks();
            scheduleBackgroundTaskRefresh();
        }, 2000);
    }

    function approvalTarget(summary) {
        const target = summary?.target;
        if (typeof target === 'string') return target;
        if (target && typeof target === 'object') {
            return target.title || target.name || target.display_name || '';
        }
        return '';
    }

    function approvalStep(step) {
        if (typeof step === 'string') return step;
        if (!step || typeof step !== 'object') return '';
        return step.title || step.action || step.name || '';
    }

    function approvalPromptMessage(tool) {
        const summary = tool.summary || {};
        const lines = [translate(
            'ai.approvalPromptIntro',
            'Review the plan below before allowing this operation to change the server.'
        )];
        const target = approvalTarget(summary);
        if (target) {
            lines.push(translate('ai.approvalPromptTarget', 'Target: {target}').replace('{target}', target));
        }
        const steps = Array.isArray(summary.steps)
            ? summary.steps.map(approvalStep).filter(Boolean)
            : [];
        if (steps.length) {
            lines.push(translate('ai.approvalPromptSteps', 'Planned steps:'));
            steps.forEach((step) => lines.push(`• ${step}`));
        }
        if (summary.risk) lines.push(summary.risk);
        return lines.join('\n\n');
    }

    function showApprovalPrompt(tool, card) {
        if (typeof window.showConfirm !== 'function') return;
        window.showConfirm(
            approvalPromptMessage(tool),
            () => decideTool(tool.id || tool.tool_run_id, tool.arguments_hash, 'approve', card),
            () => decideTool(tool.id || tool.tool_run_id, tool.arguments_hash, 'reject', card),
            translate('ai.approvalPromptTitle', 'Confirm server change')
        );
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
        showApprovalPrompt(tool, card);
    }

    async function decideTool(toolId, argumentsHash, decision, card) {
        card.querySelectorAll('button').forEach((button) => { button.disabled = true; });
        try {
            const response = await authFetch(`/api/ai/runs/${state.runId}/tools/${toolId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, arguments_hash: argumentsHash }),
            });
            const result = await jsonResponse(response);
            card.querySelector('.fw-semibold').textContent =
                result.status === 'queued'
                    ? translate('ai.queued', 'Queued')
                    : decision === 'approve'
                    ? translate('ai.approved', 'Approved')
                    : translate('ai.rejected', 'Rejected');
            card.querySelector('.d-flex')?.remove();
            setStatus(
                result.status === 'queued'
                    ? translate('ai.queued', 'Queued')
                    : translate('ai.running', 'AI task is running…')
            );
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
        state.streamedMessages.clear();
        conversation.messages.forEach((message) => appendMessage(message.role, message.content));
        clearStatus();
    }

    function newConversation() {
        stopRunWatch();
        state.conversationId = null;
        element('ai-conversation-select').value = '';
        element('ai-message-list').replaceChildren();
        state.streamedMessages.clear();
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
            state.lastSequence = '0';
            setStatus(translate('ai.running', 'AI task is running…'));
            connectEvents();
            pollRun();
        } catch (error) {
            setStatus(error.message, true);
            element('ai-send-button').disabled = !state.enabled;
        }
    }

    function rememberSequence(value) {
        const sequence = String(value || '0');
        try {
            if (BigInt(sequence) > BigInt(state.lastSequence)) state.lastSequence = sequence;
        } catch (_error) {
            // Ignore malformed sequence values from an incompatible intermediary.
        }
    }

    function parseSSEBlock(block) {
        const data = [];
        block.split(/\r?\n/).forEach((line) => {
            if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
        });
        if (!data.length) return;
        const event = JSON.parse(data.join('\n'));
        rememberSequence(event.sequence);
        handleRunEvent(event);
    }

    async function consumeSSE(response, signal) {
        const reader = response.body?.getReader();
        if (!reader) throw new Error('Streaming response body is unavailable');
        const decoder = new TextDecoder();
        let buffer = '';
        while (!signal.aborted) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            let match = buffer.match(/\r?\n\r?\n/);
            while (match && match.index != null) {
                const block = buffer.slice(0, match.index);
                buffer = buffer.slice(match.index + match[0].length);
                if (block) parseSSEBlock(block);
                match = buffer.match(/\r?\n\r?\n/);
            }
            if (done) break;
        }
    }

    async function connectEvents() {
        if (!state.runId) return;
        state.eventAbortController?.abort();
        const controller = new AbortController();
        const watchedRunId = state.runId;
        state.eventAbortController = controller;
        try {
            const response = await authFetch(
                `/api/ai/runs/${watchedRunId}/events/stream?after=${encodeURIComponent(state.lastSequence)}`,
                { headers: { Accept: 'text/event-stream' }, signal: controller.signal }
            );
            if (!response.ok) await jsonResponse(response);
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('text/event-stream')) {
                throw new Error('Server did not return an SSE event stream');
            }
            await consumeSSE(response, controller.signal);
        } catch (error) {
            if (error.name !== 'AbortError' && state.runId === watchedRunId) {
                setStatus(error.message, true);
            }
        } finally {
            if (state.eventAbortController === controller) state.eventAbortController = null;
            if (!controller.signal.aborted && state.runId === watchedRunId) {
                clearTimeout(state.reconnectTimer);
                state.reconnectTimer = setTimeout(connectEvents, 1500);
            }
        }
    }

    function handleRunEvent(event) {
        const payload = event.payload || {};
        if (event.type === 'assistant_delta') appendAssistantDelta(payload);
        if (event.type === 'assistant_message') finalizeAssistantMessage(payload);
        if (event.type === 'tool_approval_required') appendToolCard(payload);
        if (event.type === 'tool_queued') {
            setStatus(`${translate('ai.queued', 'Queued')}: ${payload.tool_name}`);
            upsertToolStatus(payload, 'queued');
            refreshBackgroundTasks();
        }
        if (event.type === 'tool_started') {
            setStatus(`${translate('ai.runningTool', 'Running tool')}: ${payload.tool_name}`);
            upsertToolStatus(payload, 'running');
            refreshBackgroundTasks();
        }
        if (event.type === 'tool_progress' && payload.message) {
            setStatus(payload.message);
            upsertToolStatus(payload, 'running', payload.message);
        }
        if (event.type === 'diagnostic_progress' && payload.message) {
            setStatus(payload.message);
            upsertToolStatus(payload, 'running', payload.message);
        }
        if (event.type === 'tool_completed') {
            upsertToolStatus(payload, 'completed');
            refreshBackgroundTasks();
        }
        if (event.type === 'tool_failed') {
            upsertToolStatus(payload, 'failed', payload.error || payload.result?.error || '');
            refreshBackgroundTasks();
        }
        if (event.type === 'tool_rejected') {
            upsertToolStatus(payload, 'rejected');
            refreshBackgroundTasks();
        }
        if (event.type === 'run_waiting_approval') {
            setStatus(translate('ai.waitingApproval', 'Waiting for your approval'));
        }
        if (event.type === 'run_failed') finishRun(payload.error, true);
        if (event.type === 'run_interrupted') finishRun(payload.error || 'Interrupted', true);
        if (event.type === 'run_completed') finishRun(translate('ai.completed', 'Completed'));
        if (event.type === 'run_completed') clearActiveStatus();
        if (event.type === 'run_failed') clearActiveStatus();
        if (event.type === 'run_interrupted') clearActiveStatus();
    }

    async function pollRun() {
        clearTimeout(state.pollTimer);
        if (!state.runId) return;
        try {
            const run = await jsonResponse(await authFetch(`/api/ai/runs/${state.runId}`));
            run.tools
                .filter((tool) => tool.status === 'pending_approval')
                .forEach(appendToolCard);
            run.tools
                .filter((tool) => ['queued', 'running', 'completed', 'failed', 'rejected'].includes(tool.status))
                .forEach((tool) => upsertToolStatus(tool, tool.status, tool.error || ''));
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
        state.eventAbortController?.abort();
        state.eventAbortController = null;
    }

    async function initialize(user) {
        if (!user || state.initialized) return;
        state.initialized = true;
        state.user = user;
        element('ai-assistant-toggle').classList.remove('d-none');
        try {
            await Promise.all([loadProviderStatus(), loadServers(), refreshBackgroundTasks()]);
            await loadConversations();
            scheduleBackgroundTaskRefresh();
        } catch (error) {
            setStatus(error.message, true);
        }
    }

    function bind() {
        element('ai-message-form')?.addEventListener('submit', sendMessage);
        element('ai-new-conversation')?.addEventListener('click', newConversation);
        element('ai-refresh-background-tasks')?.addEventListener('click', refreshBackgroundTasks);
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
