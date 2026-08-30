function profileAIFetch(url, options = {}) {
    const token = localStorage.getItem('access_token');
    return fetch(url, {
        ...options,
        headers: { ...options.headers, 'Authorization': `Bearer ${token}` }
    });
}

async function loadDiscordBotSettings() {
    const response = await profileAIFetch('/api/auth/discord-bot');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to load Discord Bot settings'));
    document.getElementById('discord-bot-enabled').checked = !!data.enabled;
    document.getElementById('discord-bot-trigger-mode').value = data.message_trigger_mode || 'mention_only';
    updateDiscordBotTriggerWarning();
    document.getElementById('discord-bot-token').value = '';
    document.getElementById('discord-bot-token-status').textContent = data.token_configured
        ? 'A Token is encrypted and saved. Leave blank to keep it.'
        : 'No Bot Token is configured.';
    document.getElementById('discord-bot-identity').textContent = data.bot_user_id
        ? `${data.username || 'Bot'} (${data.bot_user_id})`
        : '-';
    document.getElementById('discord-bot-status').textContent = data.connection_status;
    document.getElementById('discord-bot-error').textContent = data.last_error || '';
    const invite = document.getElementById('discord-bot-invite');
    if (data.invite_url) {
        invite.href = data.invite_url;
        invite.classList.remove('d-none');
    } else {
        invite.classList.add('d-none');
    }
    if (data.connection_status === 'connected') {
        await loadDiscordMenuPushGuilds();
    } else {
        resetDiscordMenuPushOptions();
    }
    await loadDiscordGlobalBinding();
}

function showDiscordBotResult(message, success) {
    const target = document.getElementById('discord-bot-result');
    target.textContent = message;
    target.className = `alert ${success ? 'alert-success' : 'alert-danger'}`;
}

function updateDiscordBotTriggerWarning() {
    const greetingMode = document.getElementById('discord-bot-trigger-mode').value === 'mention_and_greetings';
    document.getElementById('discord-bot-message-content-warning').classList.toggle('d-none', !greetingMode);
}

document.getElementById('discord-bot-trigger-mode').addEventListener('change', updateDiscordBotTriggerWarning);

function discordBotText(key, fallback) {
    const fullKey = `discordBot.${key}`;
    const translated = window.i18n?.t(fullKey);
    return typeof translated === 'string' && translated.trim() && translated !== fullKey
        ? translated
        : fallback;
}

const DISCORD_GLOBAL_CAPABILITIES = [
    ['status', 'capStatus', 'Status'],
    ['start', 'capStart', 'Start'],
    ['stop', 'capStop', 'Stop'],
    ['restart', 'capRestart', 'Restart'],
    ['update', 'capUpdate', 'CS2 update'],
    ['validate', 'capValidate', 'Validate'],
    ['plugin_browse', 'capPluginBrowse', 'Plugin browse'],
    ['plugin_install', 'capPluginInstall', 'Market install'],
    ['plugin_upgrade', 'capPluginUpgrade', 'Managed upgrade'],
    ['game_console', 'gameConsole', 'Game console input'],
    ['change_map', 'capChangeMap', 'Change map'],
    ['agent_ask', 'capAgentAsk', 'AI Agent']
];

let lastDiscordGlobalStats = { matching_server_count: 0, server_count: 0 };

function renderDiscordGlobalCapabilities() {
    const selected = new Set(discordGlobalSelectedCapabilities());
    const container = document.getElementById('discord-global-capabilities');
    container.replaceChildren();
    DISCORD_GLOBAL_CAPABILITIES.forEach(([value, key, fallback]) => {
        const column = document.createElement('div');
        column.className = 'col-md-4';
        const label = document.createElement('label');
        label.className = 'form-check';
        const input = document.createElement('input');
        input.className = 'form-check-input discord-global-capability';
        input.type = 'checkbox';
        input.value = value;
        input.addEventListener('change', updateDiscordGlobalWriteWarning);
        const text = document.createElement('span');
        text.className = 'form-check-label';
        text.textContent = discordBotText(key, fallback);
        label.append(input, text);
        column.appendChild(label);
        container.appendChild(column);
    });
    setDiscordGlobalCapabilities([...selected]);
}

function refreshDiscordSelectPlaceholder(selectId, key, fallback) {
    const select = document.getElementById(selectId);
    if (!select || !select.options.length || select.options[0].value !== '') return;
    select.options[0].textContent = discordBotText(key, fallback);
}

function refreshDiscordBotLocalizedText() {
    renderDiscordGlobalCapabilities();
    updateDiscordGlobalStats(lastDiscordGlobalStats);
    refreshDiscordGlobalAllowedSummary();
    refreshDiscordSelectPlaceholder('discord-global-guild', 'pushSelectGuild', 'Select a Guild');
    refreshDiscordSelectPlaceholder('discord-menu-push-guild', 'pushSelectGuild', 'Select a Guild');
    refreshDiscordSelectPlaceholder('discord-menu-push-channel', 'pushSelectChannel', 'Select a bound channel');
}

function discordAllowedGrantPaths({ userCount, roleCount, allowChannelManagers, allowServerAdministrators }) {
    const parts = [];
    if (userCount) {
        parts.push(discordBotText('allowedUsers', '{count} user IDs').replace('{count}', userCount));
    }
    if (roleCount) {
        parts.push(discordBotText('allowedRoles', '{count} roles').replace('{count}', roleCount));
    }
    if (allowServerAdministrators) {
        parts.push(discordBotText('allowedServerAdministratorsOn', 'Discord server administrators'));
    }
    if (allowChannelManagers) {
        parts.push(discordBotText('allowedChannelManagersOn', 'current-channel managers'));
    }
    if (!parts.length) {
        return discordBotText(
            'allowedNone',
            'Currently allowed: no grant path is selected. Enable at least one user, role, server-administrator, or channel-manager option before turning the binding on.'
        );
    }
    return discordBotText('allowedSummary', 'Currently allowed: {paths}').replace('{paths}', parts.join(', '));
}

function refreshDiscordGlobalAllowedSummary() {
    const summary = document.getElementById('discord-global-allowed-summary');
    if (!summary) return;
    const userCount = document.getElementById('discord-global-users').value
        .split(/[\s,]+/).map((item) => item.trim()).filter(Boolean).length;
    const roleCount = document.getElementById('discord-global-roles').selectedOptions.length;
    summary.textContent = discordAllowedGrantPaths({
        userCount,
        roleCount,
        allowChannelManagers: document.getElementById('discord-global-channel-managers').checked,
        allowServerAdministrators: document.getElementById('discord-global-server-administrators').checked
    });
}

function discordGlobalSelectedCapabilities() {
    return [...document.querySelectorAll('.discord-global-capability:checked')].map(item => item.value);
}

function setDiscordGlobalCapabilities(values) {
    const selected = new Set(values || []);
    document.querySelectorAll('.discord-global-capability').forEach((item) => {
        item.checked = selected.has(item.value);
    });
    updateDiscordGlobalWriteWarning();
}

function updateDiscordGlobalWriteWarning() {
    const readonly = new Set(['status', 'plugin_browse', 'agent_ask']);
    const dangerous = discordGlobalSelectedCapabilities().some(item => !readonly.has(item));
    document.getElementById('discord-global-write-warning').classList.toggle('d-none', !dangerous);
}

function setDiscordGlobalMultiOptions(select, items, selectedValues, prefix = '') {
    const selected = new Set(selectedValues || []);
    select.replaceChildren();
    items.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.id;
        option.textContent = `${prefix}${item.name}`;
        option.selected = selected.has(item.id);
        select.appendChild(option);
    });
    select.disabled = items.length === 0;
}

function setDiscordGlobalStatus(message, isError = false) {
    const target = document.getElementById('discord-global-status');
    target.textContent = message || '';
    target.className = `form-text mt-2${isError ? ' text-danger' : ''}`;
}

function updateDiscordGlobalStats(data) {
    lastDiscordGlobalStats = data || lastDiscordGlobalStats;
    const template = discordBotText(
        'globalStats',
        '{matching}/{total} existing servers currently match this template. New servers inherit it automatically.'
    );
    document.getElementById('discord-global-stats').textContent = template
        .replace('{matching}', lastDiscordGlobalStats.matching_server_count || 0)
        .replace('{total}', lastDiscordGlobalStats.server_count || 0);
}

async function loadDiscordGlobalOptions(guildId = '', selectedChannels = [], selectedRoles = []) {
    const guildSelect = document.getElementById('discord-global-guild');
    const channelSelect = document.getElementById('discord-global-channels');
    const roleSelect = document.getElementById('discord-global-roles');
    const suffix = guildId ? `?guild_id=${encodeURIComponent(guildId)}` : '';
    try {
        const response = await profileAIFetch(`/api/auth/discord-bot/global-options${suffix}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to load global Discord options'));
        setDiscordMenuSelectOptions(
            guildSelect,
            discordBotText('pushSelectGuild', 'Select a Guild'),
            data.guilds || []
        );
        guildSelect.value = guildId || '';
        setDiscordGlobalMultiOptions(channelSelect, data.channels || [], selectedChannels, '#');
        setDiscordGlobalMultiOptions(roleSelect, data.roles || [], selectedRoles);
    } catch (error) {
        setDiscordMenuSelectOptions(
            guildSelect,
            discordBotText('pushSelectGuild', 'Select a Guild'),
            []
        );
        setDiscordGlobalMultiOptions(channelSelect, [], []);
        setDiscordGlobalMultiOptions(roleSelect, [], []);
        setDiscordGlobalStatus(error.message, true);
    }
}

async function loadDiscordGlobalBinding() {
    setDiscordGlobalStatus(discordBotText('globalLoading', 'Loading global template…'));
    try {
        const response = await profileAIFetch('/api/auth/discord-bot/global-settings');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to load global Discord template'));
        document.getElementById('discord-global-enabled').checked = !!data.enabled;
        document.getElementById('discord-global-channel-managers').checked = !!data.allow_channel_managers;
        document.getElementById('discord-global-server-administrators').checked = !!data.allow_server_administrators;
        document.getElementById('discord-global-users').value = (data.user_ids || []).join('\n');
        setDiscordGlobalCapabilities(data.capabilities || []);
        updateDiscordGlobalStats(data);
        await loadDiscordGlobalOptions(
            data.guild_id || '',
            data.channel_ids || [],
            data.role_ids || []
        );
        refreshDiscordGlobalAllowedSummary();
        if (!document.getElementById('discord-global-status').classList.contains('text-danger')) {
            setDiscordGlobalStatus('');
        }
    } catch (error) {
        setDiscordGlobalStatus(error.message, true);
    }
}

async function saveDiscordGlobalBinding(syncExisting) {
    if (syncExisting && !confirm(discordBotText(
        'globalSyncConfirm',
        'Overwrite Discord settings on every server you own? Per-server customizations will be lost.'
    ))) return;
    const saveButton = document.getElementById('discord-global-save');
    const syncButton = document.getElementById('discord-global-sync');
    saveButton.disabled = true;
    syncButton.disabled = true;
    setDiscordGlobalStatus(discordBotText('globalSaving', 'Saving global template…'));
    try {
        const selectedValues = (id) => [...document.getElementById(id).selectedOptions]
            .map(option => option.value);
        const userIds = document.getElementById('discord-global-users').value
            .split(/[\s,]+/).map(item => item.trim()).filter(Boolean);
        const guildId = document.getElementById('discord-global-guild').value;
        const payload = {
            enabled: document.getElementById('discord-global-enabled').checked,
            guild_id: guildId || null,
            channel_ids: selectedValues('discord-global-channels'),
            role_ids: selectedValues('discord-global-roles'),
            user_ids: [...new Set(userIds)],
            allow_channel_managers: document.getElementById('discord-global-channel-managers').checked,
            allow_server_administrators: document.getElementById('discord-global-server-administrators').checked,
            capabilities: discordGlobalSelectedCapabilities(),
            response_visibility: 'public',
            sync_existing_servers: syncExisting
        };
        const response = await profileAIFetch('/api/auth/discord-bot/global-settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to save global Discord template'));
        updateDiscordGlobalStats(data);
        const key = syncExisting ? 'globalSyncSuccess' : 'globalSaveSuccess';
        const fallback = syncExisting
            ? `Template saved and synchronized to ${data.synced_server_count || 0} servers.`
            : 'Global template saved. Existing servers were not changed.';
        setDiscordGlobalStatus(discordBotText(key, fallback)
            .replace('{count}', data.synced_server_count || 0));
        if (syncExisting && document.getElementById('discord-bot-status').textContent === 'connected') {
            await loadDiscordMenuPushGuilds();
        }
    } catch (error) {
        setDiscordGlobalStatus(error.message, true);
    } finally {
        saveButton.disabled = false;
        syncButton.disabled = false;
    }
}

renderDiscordGlobalCapabilities();
refreshDiscordGlobalAllowedSummary();
['discord-global-users', 'discord-global-roles', 'discord-global-channel-managers', 'discord-global-server-administrators']
    .forEach((id) => {
        const target = document.getElementById(id);
        if (!target) return;
        target.addEventListener('input', refreshDiscordGlobalAllowedSummary);
        target.addEventListener('change', refreshDiscordGlobalAllowedSummary);
    });
window.addEventListener('i18nReady', refreshDiscordBotLocalizedText);
window.addEventListener('localeChanged', () => {
    if (window.i18n?.isInitialized) refreshDiscordBotLocalizedText();
});

document.getElementById('discord-global-guild').addEventListener('change', async (event) => {
    await loadDiscordGlobalOptions(event.target.value, [], []);
    refreshDiscordGlobalAllowedSummary();
});

document.getElementById('discord-global-preset-readonly').addEventListener('click', () => {
    setDiscordGlobalCapabilities(['status', 'plugin_browse']);
});

document.getElementById('discord-global-preset-operations').addEventListener('click', () => {
    setDiscordGlobalCapabilities(['status', 'start', 'stop', 'restart', 'update', 'validate', 'plugin_browse']);
});

document.getElementById('discord-global-save').addEventListener('click', () => {
    saveDiscordGlobalBinding(false);
});

document.getElementById('discord-global-sync').addEventListener('click', () => {
    saveDiscordGlobalBinding(true);
});

function setDiscordMenuPushStatus(message, isError = false) {
    const target = document.getElementById('discord-menu-push-status');
    target.textContent = message || '';
    target.className = `form-text mt-2${isError ? ' text-danger' : ''}`;
}

function setDiscordMenuSelectOptions(select, placeholder, items) {
    select.replaceChildren();
    const initial = document.createElement('option');
    initial.value = '';
    initial.textContent = placeholder;
    select.appendChild(initial);
    items.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.id;
        option.textContent = item.name;
        select.appendChild(option);
    });
    select.disabled = items.length === 0;
}

function resetDiscordMenuPushOptions() {
    setDiscordMenuSelectOptions(
        document.getElementById('discord-menu-push-guild'),
        discordBotText('pushSelectGuild', 'Select a Guild'),
        []
    );
    setDiscordMenuSelectOptions(
        document.getElementById('discord-menu-push-channel'),
        discordBotText('pushSelectChannel', 'Select a bound channel'),
        []
    );
    document.getElementById('discord-menu-push-button').disabled = true;
    setDiscordMenuPushStatus('');
}

async function loadDiscordMenuPushGuilds() {
    resetDiscordMenuPushOptions();
    setDiscordMenuPushStatus(discordBotText('pushLoading', 'Loading bound channels…'));
    try {
        const response = await profileAIFetch('/api/auth/discord-bot/menu-options');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to load menu channels'));
        const guilds = Array.isArray(data.guilds) ? data.guilds : [];
        const select = document.getElementById('discord-menu-push-guild');
        setDiscordMenuSelectOptions(
            select,
            discordBotText('pushSelectGuild', 'Select a Guild'),
            guilds
        );
        setDiscordMenuPushStatus(guilds.length
            ? ''
            : discordBotText('pushNoChannels', 'No enabled server binding has a pushable channel.'));
        if (guilds.length === 1) {
            select.value = guilds[0].id;
            await loadDiscordMenuPushChannels(guilds[0].id);
        }
    } catch (error) {
        setDiscordMenuPushStatus(error.message, true);
    }
}

async function loadDiscordMenuPushChannels(guildId) {
    const channelSelect = document.getElementById('discord-menu-push-channel');
    setDiscordMenuSelectOptions(
        channelSelect,
        discordBotText('pushSelectChannel', 'Select a bound channel'),
        []
    );
    document.getElementById('discord-menu-push-button').disabled = true;
    if (!guildId) return;
    setDiscordMenuPushStatus(discordBotText('pushLoading', 'Loading bound channels…'));
    try {
        const response = await profileAIFetch(`/api/auth/discord-bot/menu-options?guild_id=${encodeURIComponent(guildId)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to load menu channels'));
        const channels = Array.isArray(data.channels) ? data.channels : [];
        setDiscordMenuSelectOptions(
            channelSelect,
            discordBotText('pushSelectChannel', 'Select a bound channel'),
            channels
        );
        setDiscordMenuPushStatus(channels.length
            ? ''
            : discordBotText('pushNoChannels', 'No enabled server binding has a pushable channel.'));
        if (channels.length === 1) {
            channelSelect.value = channels[0].id;
            document.getElementById('discord-menu-push-button').disabled = false;
        }
    } catch (error) {
        setDiscordMenuPushStatus(error.message, true);
    }
}

document.getElementById('discord-menu-push-guild').addEventListener('change', (event) => {
    loadDiscordMenuPushChannels(event.target.value);
});

document.getElementById('discord-menu-push-channel').addEventListener('change', (event) => {
    document.getElementById('discord-menu-push-button').disabled = !event.target.value;
});

document.getElementById('discord-menu-push-button').addEventListener('click', async () => {
    const button = document.getElementById('discord-menu-push-button');
    const guildId = document.getElementById('discord-menu-push-guild').value;
    const channelId = document.getElementById('discord-menu-push-channel').value;
    if (!guildId || !channelId) return;
    button.disabled = true;
    setDiscordMenuPushStatus(discordBotText('pushSending', 'Pushing menu…'));
    try {
        const response = await profileAIFetch('/api/auth/discord-bot/menu', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ guild_id: guildId, channel_id: channelId })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to push menu'));
        setDiscordMenuPushStatus(discordBotText('pushSuccess', 'The menu was pushed and will expire in five minutes.'));
    } catch (error) {
        setDiscordMenuPushStatus(error.message, true);
    } finally {
        button.disabled = !document.getElementById('discord-menu-push-channel').value;
    }
});

document.getElementById('discord-bot-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.getElementById('discord-bot-save');
    button.disabled = true;
    try {
        const token = document.getElementById('discord-bot-token').value.trim();
        const body = {
            enabled: document.getElementById('discord-bot-enabled').checked,
            message_trigger_mode: document.getElementById('discord-bot-trigger-mode').value,
        };
        if (token) body.token = token;
        const response = await profileAIFetch('/api/auth/discord-bot', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to save Discord Bot settings'));
        showDiscordBotResult('Discord Bot settings saved. Invite it to a Discord server before selecting channels.', true);
        await loadDiscordBotSettings();
    } catch (error) {
        showDiscordBotResult(error.message, false);
    } finally {
        button.disabled = false;
    }
});

document.getElementById('discord-bot-test').addEventListener('click', async () => {
    const token = document.getElementById('discord-bot-token').value.trim();
    try {
        const response = await profileAIFetch('/api/auth/discord-bot/test', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(token ? { token } : {})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) throw new Error(window.formatApiErrorDetail(data, 'Token test failed'));
        showDiscordBotResult(`${data.message}: ${data.username} (${data.bot_user_id})`, true);
    } catch (error) {
        showDiscordBotResult(error.message, false);
    }
});

document.getElementById('discord-bot-clear').addEventListener('click', async () => {
    if (!confirm('Clear the saved Bot Token and stop the Gateway connection? Server bindings will be retained but disabled.')) return;
    try {
        const response = await profileAIFetch('/api/auth/discord-bot', { method: 'DELETE' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(window.formatApiErrorDetail(data, 'Failed to clear Bot Token'));
        showDiscordBotResult('Bot Token cleared. Existing server bindings are retained but inactive.', true);
        await loadDiscordBotSettings();
    } catch (error) {
        showDiscordBotResult(error.message, false);
    }
});

function showProfileAIResult(message, success) {
    const target = document.getElementById('profile-ai-result');
    target.textContent = message;
    target.className = `alert ${success ? 'alert-success' : 'alert-danger'}`;
}

function profileAIOptionalNumber(id) {
    const value = document.getElementById(id).value.trim();
    return value === '' ? null : Number(value);
}

function profileAIOptionalBoolean(id) {
    const value = document.getElementById(id).value;
    return value === '' ? null : value === 'true';
}

function updateProfileAIMode() {
    const custom = document.getElementById('profile-ai-mode').value === 'custom';
    document.getElementById('profile-ai-custom-fields').style.display = custom ? 'block' : 'none';
    document.getElementById('profile-ai-test').style.display = custom ? 'inline-block' : 'none';
}

async function loadUserAISettings() {
    const response = await profileAIFetch('/api/auth/ai-settings');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to load AI settings');
    document.getElementById('profile-ai-mode').value = data.mode;
    document.getElementById('profile-ai-base-url').value = data.base_url || '';
    document.getElementById('profile-ai-model').value = data.model || '';
    document.getElementById('profile-ai-protocol').value = data.api_protocol || 'chat_completions';
    document.getElementById('profile-ai-reasoning').value = data.reasoning_effort || '';
    document.getElementById('profile-ai-temperature').value = data.temperature ?? '';
    document.getElementById('profile-ai-top-p').value = data.top_p ?? '';
    document.getElementById('profile-ai-max-tokens').value = data.max_completion_tokens || 2048;
    document.getElementById('profile-ai-token-field').value = data.token_limit_parameter || 'max_completion_tokens';
    document.getElementById('profile-ai-frequency-penalty').value = data.frequency_penalty ?? '';
    document.getElementById('profile-ai-presence-penalty').value = data.presence_penalty ?? '';
    document.getElementById('profile-ai-verbosity').value = data.verbosity || '';
    document.getElementById('profile-ai-parallel-tools').value = data.parallel_tool_calls == null
        ? '' : String(data.parallel_tool_calls);
    document.getElementById('profile-ai-key').value = '';
    document.getElementById('profile-ai-clear-key').checked = false;
    document.getElementById('profile-ai-key-status').textContent = data.api_key_configured
        ? (window.i18n?.t('ai.keyConfigured') || 'A key is configured')
        : (window.i18n?.t('ai.keyMissing') || 'No key configured');
    document.getElementById('profile-ai-test-state').textContent =
        `${data.api_protocol === 'responses' ? 'Responses' : 'Chat Completions'} · SSE: ${data.streaming_tested ? '✓' : '—'} · Text: ${data.provider_tested ? '✓' : '—'} · tool_calls: ${data.tool_calling_tested ? '✓' : '—'} · ` +
        (data.effective_enabled ? (window.i18n?.t('ai.providerReady') || 'Provider ready') : (window.i18n?.t('ai.providerUnavailable') || 'Unavailable'));
    updateProfileAIMode();
}

document.getElementById('profile-ai-mode').addEventListener('change', updateProfileAIMode);
document.getElementById('profile-ai-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
        mode: document.getElementById('profile-ai-mode').value,
        base_url: document.getElementById('profile-ai-base-url').value.trim() || null,
        model: document.getElementById('profile-ai-model').value.trim() || null,
        api_protocol: document.getElementById('profile-ai-protocol').value,
        reasoning_effort: document.getElementById('profile-ai-reasoning').value || null,
        temperature: profileAIOptionalNumber('profile-ai-temperature'),
        top_p: profileAIOptionalNumber('profile-ai-top-p'),
        max_completion_tokens: Number(document.getElementById('profile-ai-max-tokens').value),
        token_limit_parameter: document.getElementById('profile-ai-token-field').value,
        frequency_penalty: profileAIOptionalNumber('profile-ai-frequency-penalty'),
        presence_penalty: profileAIOptionalNumber('profile-ai-presence-penalty'),
        verbosity: document.getElementById('profile-ai-verbosity').value || null,
        parallel_tool_calls: profileAIOptionalBoolean('profile-ai-parallel-tools'),
        clear_api_key: document.getElementById('profile-ai-clear-key').checked,
    };
    const key = document.getElementById('profile-ai-key').value.trim();
    if (key) payload.api_key = key;
    try {
        const response = await profileAIFetch('/api/auth/ai-settings', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Unable to save AI settings');
        showProfileAIResult(window.i18n?.t('ai.saved') || 'AI settings saved', true);
        await loadUserAISettings();
    } catch (error) {
        showProfileAIResult(error.message, false);
    }
});

document.getElementById('profile-ai-test').addEventListener('click', async () => {
    const button = document.getElementById('profile-ai-test');
    button.disabled = true;
    try {
        const response = await profileAIFetch('/api/auth/ai-settings/test', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.detail || data.message || 'Provider test failed');
        showProfileAIResult(data.message, true);
        await loadUserAISettings();
    } catch (error) {
        showProfileAIResult(error.message, false);
        await loadUserAISettings().catch(() => {});
    } finally {
        button.disabled = false;
    }
});

// Load user information
async function loadUserInfo() {
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/me', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const user = await response.json();
            document.getElementById('user-username').textContent = user.username;
            document.getElementById('user-email').textContent = user.email;
            
            const roleBadge = document.getElementById('user-role-badge');
            if (user.is_admin) {
                roleBadge.textContent = window.i18n ? window.i18n.t('profile.admin') : 'Admin';
                roleBadge.className = 'badge bg-danger';
            } else {
                roleBadge.textContent = window.i18n ? window.i18n.t('profile.user') : 'User';
                roleBadge.className = 'badge bg-primary';
            }
            
            const createdDate = new Date(user.created_at);
            document.getElementById('user-created').textContent = createdDate.toLocaleString();
        } else if (response.status === 401) {
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Failed to load user info:', error);
    }
}

// Load Steam API key
async function loadSteamApiKey() {
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/steam-api-key', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.steam_api_key) {
                document.getElementById('profile-steam-api-key').value = data.steam_api_key;
            }
        } else if (response.status === 401) {
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Failed to load Steam API key:', error);
    }
}

// Load GitHub token status
async function loadGitHubTokenStatus() {
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/github-token-status', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            const statusDiv = document.getElementById('github-token-status');
            const prefixElement = document.getElementById('github-token-prefix');
            
            if (data.has_token && data.token_prefix) {
                // Show the status indicator with token prefix
                prefixElement.textContent = data.token_prefix;
                statusDiv.classList.remove('d-none');
            } else {
                // Hide the status indicator if no token
                statusDiv.classList.add('d-none');
            }
        } else if (response.status === 401) {
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Failed to load GitHub token status:', error);
    }
}

// Load S3 settings
async function loadS3Settings() {
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/s3-settings', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            document.getElementById('s3-enabled').checked = !!data.enabled;
            document.getElementById('s3-endpoint-url').value = data.endpoint_url || '';
            document.getElementById('s3-region').value = data.region || '';
            document.getElementById('s3-bucket').value = data.bucket || '';
            document.getElementById('s3-prefix').value = data.prefix || '';
            document.getElementById('s3-retention-count').value = data.retention_count ?? 10;
            document.getElementById('s3-access-key-id').value = data.access_key_id || '';
            document.getElementById('s3-use-ssl').checked = data.use_ssl !== false;
            document.getElementById('s3-secret-access-key').value = '';
            document.getElementById('s3-clear-secret').checked = false;

            const statusBox = document.getElementById('s3-config-status');
            const statusText = document.getElementById('s3-config-status-text');
            const secretStatus = document.getElementById('s3-secret-status');

            if (data.is_configured) {
                statusBox.className = 'alert alert-success py-2';
                statusText.textContent = window.i18n ? window.i18n.t('profile.s3Configured') : 'S3-compatible storage is configured';
            } else if (data.enabled) {
                statusBox.className = 'alert alert-warning py-2';
                statusText.textContent = window.i18n ? window.i18n.t('profile.s3Incomplete') : 'S3-compatible storage is enabled but incomplete';
            } else {
                statusBox.className = 'alert alert-secondary py-2';
                statusText.textContent = window.i18n ? window.i18n.t('profile.s3NotConfigured') : 'S3-compatible storage is not configured';
            }

            secretStatus.textContent = data.has_secret
                ? (window.i18n ? window.i18n.t('profile.s3SecretSaved') : 'Secret key is saved. Leave blank to keep it.')
                : (window.i18n ? window.i18n.t('profile.s3SecretMissing') : 'No secret key saved.');
        } else if (response.status === 401) {
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Failed to load S3 settings:', error);
    }
}

// Load CAPTCHA for S3 settings
async function loadS3Captcha() {
    try {
        const response = await fetch('/api/captcha/image/new');
        const token = response.headers.get('X-Captcha-Token');
        const blob = await response.blob();
        const imageUrl = URL.createObjectURL(blob);

        document.getElementById('s3-captcha-image').src = imageUrl;
        document.getElementById('s3-captcha-token').value = token;
    } catch (error) {
        console.error('Failed to load S3 CAPTCHA:', error);
    }
}

// Load CAPTCHA for profile form
async function loadProfileCaptcha() {
    try {
        const response = await fetch('/api/captcha/image/new');
        const token = response.headers.get('X-Captcha-Token');
        const blob = await response.blob();
        const imageUrl = URL.createObjectURL(blob);
        
        document.getElementById('profile-captcha-image').src = imageUrl;
        document.getElementById('profile-captcha-token').value = token;
    } catch (error) {
        console.error('Failed to load CAPTCHA:', error);
    }
}

// Load CAPTCHA for password form
async function loadPasswordCaptcha() {
    try {
        const response = await fetch('/api/captcha/image/new');
        const token = response.headers.get('X-Captcha-Token');
        const blob = await response.blob();
        const imageUrl = URL.createObjectURL(blob);
        
        document.getElementById('password-captcha-image').src = imageUrl;
        document.getElementById('password-captcha-token').value = token;
    } catch (error) {
        console.error('Failed to load CAPTCHA:', error);
    }
}

// Refresh CAPTCHAs
document.getElementById('profile-refresh-captcha').addEventListener('click', loadProfileCaptcha);
document.getElementById('profile-captcha-image').addEventListener('click', loadProfileCaptcha);
document.getElementById('password-refresh-captcha').addEventListener('click', loadPasswordCaptcha);
document.getElementById('password-captcha-image').addEventListener('click', loadPasswordCaptcha);
document.getElementById('s3-refresh-captcha').addEventListener('click', loadS3Captcha);
document.getElementById('s3-captcha-image').addEventListener('click', loadS3Captcha);

// Handle profile update
document.getElementById('profile-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const submitBtn = document.getElementById('profile-submit-btn');
    const errorDiv = document.getElementById('profile-error-message');
    const successDiv = document.getElementById('profile-success-message');
    
    submitBtn.disabled = true;
    const updatingText = window.i18n ? window.i18n.t('profile.updating') : 'Updating...';
    submitBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${updatingText}`;
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');
    
    try {
        const token = localStorage.getItem('access_token');
        const email = document.getElementById('profile-email').value.trim();
        const steamApiKey = document.getElementById('profile-steam-api-key').value.trim();
        const githubToken = document.getElementById('profile-github-token').value.trim();
        
        const requestBody = {
            captcha_token: document.getElementById('profile-captcha-token').value,
            captcha_code: document.getElementById('profile-captcha').value
        };
        
        if (email) {
            requestBody.email = email;
        }
        
        // Always include steam_api_key (even if empty, to allow clearing)
        requestBody.steam_api_key = steamApiKey;
        
        // Always include github_token (even if empty, to allow clearing)
        requestBody.github_token = githubToken;
        
        const response = await fetch('/api/auth/profile', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(requestBody)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            let successMsg = window.i18n ? window.i18n.t('profile.updateSuccess') : 'Profile updated successfully';
            // Add token confirmation to success message if token was provided
            if (githubToken) {
                successMsg += ' GitHub token has been securely saved.';
            }
            successDiv.textContent = successMsg;
            successDiv.classList.remove('d-none');
            document.getElementById('profile-email').value = '';
            document.getElementById('profile-github-token').value = ''; // Clear the token field for security
            loadUserInfo();
            loadSteamApiKey(); // Reload Steam API key to reflect changes
            loadGitHubTokenStatus(); // Reload GitHub token status to show indicator
        } else {
            errorDiv.textContent = data.detail || (window.i18n ? window.i18n.t('profile.updateFailed') : 'Failed to update profile');
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.updateFailed') : 'Failed to update profile';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        submitBtn.disabled = false;
        const updateText = window.i18n ? window.i18n.t('profile.updateButton') : 'Update Profile';
        submitBtn.innerHTML = `<i class="bi bi-check-circle"></i> ${updateText}`;
        loadProfileCaptcha();
    }
});

// Handle S3 settings update
document.getElementById('s3-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const submitBtn = document.getElementById('s3-submit-btn');
    const errorDiv = document.getElementById('s3-error-message');
    const successDiv = document.getElementById('s3-success-message');

    submitBtn.disabled = true;
    submitBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${window.i18n ? window.i18n.t('common.saving') : 'Saving...'}`;
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');

    try {
        const token = localStorage.getItem('access_token');
        const retentionValue = document.getElementById('s3-retention-count').value.trim();
        const response = await fetch('/api/auth/s3-settings', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                enabled: document.getElementById('s3-enabled').checked,
                endpoint_url: document.getElementById('s3-endpoint-url').value.trim(),
                region: document.getElementById('s3-region').value.trim(),
                bucket: document.getElementById('s3-bucket').value.trim(),
                prefix: document.getElementById('s3-prefix').value.trim(),
                retention_count: retentionValue === '' ? 10 : Number.parseInt(retentionValue, 10),
                access_key_id: document.getElementById('s3-access-key-id').value.trim(),
                secret_access_key: document.getElementById('s3-secret-access-key').value.trim(),
                use_ssl: document.getElementById('s3-use-ssl').checked,
                clear_secret: document.getElementById('s3-clear-secret').checked,
                captcha_token: document.getElementById('s3-captcha-token').value,
                captcha_code: document.getElementById('s3-captcha').value
            })
        });

        const data = await response.json();
        if (response.ok) {
            successDiv.textContent = window.i18n ? window.i18n.t('profile.s3SaveSuccess') : 'Storage settings saved successfully';
            successDiv.classList.remove('d-none');
            await loadS3Settings();
        } else {
            errorDiv.textContent = data.detail || (window.i18n ? window.i18n.t('profile.s3SaveFailed') : 'Failed to save storage settings');
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.s3SaveFailed') : 'Failed to save storage settings';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = `<i class="bi bi-check-circle"></i> ${window.i18n ? window.i18n.t('profile.s3Save') : 'Save Storage Settings'}`;
        document.getElementById('s3-captcha').value = '';
        loadS3Captcha();
    }
});

// Handle S3 connection test
document.getElementById('s3-test-btn').addEventListener('click', async () => {
    const testBtn = document.getElementById('s3-test-btn');
    const errorDiv = document.getElementById('s3-error-message');
    const successDiv = document.getElementById('s3-success-message');

    testBtn.disabled = true;
    testBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${window.i18n ? window.i18n.t('profile.s3Testing') : 'Testing...'}`;
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');
    errorDiv.style.whiteSpace = 'pre-line';
    successDiv.style.whiteSpace = 'pre-line';

    const formatS3TestMessage = (data, fallback) => {
        const lines = [data.message || fallback];
        if (Array.isArray(data.steps) && data.steps.length > 0) {
            lines.push('');
            data.steps.forEach((step) => {
                const marker = step.status === 'success' ? '[OK]' : '[FAIL]';
                lines.push(`${marker} ${step.name}: ${step.message}`);
            });
        }
        return lines.join('\n');
    };

    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/s3-settings/test', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        const data = await response.json();
        if (response.ok && data.success) {
            successDiv.textContent = formatS3TestMessage(
                data,
                window.i18n ? window.i18n.t('profile.s3TestSuccess') : 'Storage access test succeeded'
            );
            successDiv.classList.remove('d-none');
        } else {
            errorDiv.textContent = formatS3TestMessage(
                data,
                data.detail || (window.i18n ? window.i18n.t('profile.s3TestFailed') : 'Storage access test failed')
            );
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.s3TestFailed') : 'Storage access test failed';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        testBtn.disabled = false;
        testBtn.innerHTML = `<i class="bi bi-wifi"></i> ${window.i18n ? window.i18n.t('profile.s3Test') : 'Test Storage Access'}`;
    }
});

// Handle password reset
document.getElementById('password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const submitBtn = document.getElementById('password-submit-btn');
    const errorDiv = document.getElementById('password-error-message');
    const successDiv = document.getElementById('password-success-message');
    
    const newPassword = document.getElementById('new-password').value;
    const confirmPassword = document.getElementById('confirm-password').value;
    
    // Check if passwords match
    if (newPassword !== confirmPassword) {
        errorDiv.textContent = window.i18n ? window.i18n.t('profile.passwordsMismatch') : 'Passwords do not match';
        errorDiv.classList.remove('d-none');
        return;
    }
    
    submitBtn.disabled = true;
    const resettingText = window.i18n ? window.i18n.t('profile.resetting') : 'Resetting...';
    submitBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${resettingText}`;
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');
    
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/reset-password', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                current_password: document.getElementById('current-password').value,
                new_password: newPassword,
                confirm_password: confirmPassword,
                captcha_token: document.getElementById('password-captcha-token').value,
                captcha_code: document.getElementById('password-captcha').value
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            const successMsg = window.i18n ? window.i18n.t('profile.passwordResetSuccess') : 'Password reset successfully';
            successDiv.textContent = successMsg;
            successDiv.classList.remove('d-none');
            document.getElementById('password-form').reset();
        } else {
            errorDiv.textContent = data.detail || (window.i18n ? window.i18n.t('profile.passwordResetFailed') : 'Failed to reset password');
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.passwordResetFailed') : 'Failed to reset password';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        submitBtn.disabled = false;
        const resetText = window.i18n ? window.i18n.t('profile.resetPasswordButton') : 'Reset Password';
        submitBtn.innerHTML = `<i class="bi bi-shield-check"></i> ${resetText}`;
        loadPasswordCaptcha();
    }
});

// API Key Management
// Load CAPTCHA for API key operations
async function loadApiKeyCaptcha() {
    try {
        const response = await fetch('/api/captcha/image/new');
        const token = response.headers.get('X-Captcha-Token');
        const blob = await response.blob();
        const imageUrl = URL.createObjectURL(blob);
        
        document.getElementById('apikey-captcha-image').src = imageUrl;
        document.getElementById('apikey-captcha-token').value = token;
    } catch (error) {
        console.error('Failed to load CAPTCHA:', error);
    }
}

// Load current API key
async function loadApiKey() {
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/api-key', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            document.getElementById('apikey-value').textContent = data.api_key;
            document.getElementById('apikey-display').classList.remove('d-none');
            document.getElementById('no-apikey-message').classList.add('d-none');
            document.getElementById('generate-apikey-btn').classList.add('d-none');
            document.getElementById('regenerate-apikey-btn').classList.remove('d-none');
            document.getElementById('revoke-apikey-btn').classList.remove('d-none');
        } else if (response.status === 404) {
            // No API key generated yet
            document.getElementById('apikey-display').classList.add('d-none');
            document.getElementById('no-apikey-message').classList.remove('d-none');
            document.getElementById('generate-apikey-btn').classList.remove('d-none');
            document.getElementById('regenerate-apikey-btn').classList.add('d-none');
            document.getElementById('revoke-apikey-btn').classList.add('d-none');
        } else if (response.status === 401) {
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Failed to load API key:', error);
    }
}

// Copy API key to clipboard with fallback for older browsers
document.getElementById('copy-apikey-btn').addEventListener('click', () => {
    const apiKey = document.getElementById('apikey-value').textContent;
    const btn = document.getElementById('copy-apikey-btn');
    const originalHTML = btn.innerHTML;
    
    // Try modern Clipboard API first
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(apiKey).then(() => {
            btn.innerHTML = '<i class="bi bi-check"></i>';
            setTimeout(() => {
                btn.innerHTML = originalHTML;
            }, 2000);
        }).catch(err => {
            console.error('Failed to copy:', err);
            // Fallback to old method
            copyToClipboardFallback(apiKey, btn, originalHTML);
        });
    } else {
        // Use fallback for older browsers or non-HTTPS contexts
        copyToClipboardFallback(apiKey, btn, originalHTML);
    }
});

// Fallback clipboard copy function for older browsers
function copyToClipboardFallback(text, btn, originalHTML) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            btn.innerHTML = '<i class="bi bi-check"></i>';
            setTimeout(() => {
                btn.innerHTML = originalHTML;
            }, 2000);
        } else {
            alert('Failed to copy. Please copy manually: ' + text);
        }
    } catch (err) {
        console.error('Fallback copy failed:', err);
        alert('Failed to copy. Please copy manually: ' + text);
    } finally {
        document.body.removeChild(textArea);
    }
}

// Generate/Regenerate API key
async function generateApiKey() {
    const errorDiv = document.getElementById('apikey-error-message');
    const successDiv = document.getElementById('apikey-success-message');
    const generateBtn = document.getElementById('generate-apikey-btn');
    const regenerateBtn = document.getElementById('regenerate-apikey-btn');
    
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');
    
    const activeBtn = generateBtn.classList.contains('d-none') ? regenerateBtn : generateBtn;
    activeBtn.disabled = true;
    const generatingText = window.i18n ? window.i18n.t('profile.generating') : 'Generating...';
    activeBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${generatingText}`;
    
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/api-key/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                captcha_token: document.getElementById('apikey-captcha-token').value,
                captcha_code: document.getElementById('apikey-captcha').value
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            const successMsg = window.i18n ? window.i18n.t('profile.apiKeyGenerated') : 'API key generated successfully';
            successDiv.textContent = successMsg;
            successDiv.classList.remove('d-none');
            document.getElementById('apikey-captcha').value = '';
            document.getElementById('apikey-captcha-token').value = '';  // Clear token to prevent reuse
            loadApiKey();
        } else {
            errorDiv.textContent = data.detail || (window.i18n ? window.i18n.t('profile.apiKeyGenerateFailed') : 'Failed to generate API key');
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.apiKeyGenerateFailed') : 'Failed to generate API key';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        activeBtn.disabled = false;
        const btnText = activeBtn === generateBtn ? 
            (window.i18n ? window.i18n.t('profile.generateApiKey') : 'Generate API Key') :
            (window.i18n ? window.i18n.t('profile.regenerateApiKey') : 'Regenerate');
        const btnIcon = activeBtn === generateBtn ? 'plus-circle' : 'arrow-clockwise';
        activeBtn.innerHTML = `<i class="bi bi-${btnIcon}"></i> ${btnText}`;
        loadApiKeyCaptcha();
    }
}

// Revoke API key
document.getElementById('revoke-apikey-btn').addEventListener('click', async () => {
    if (!confirm(window.i18n ? window.i18n.t('profile.confirmRevokeApiKey') : 'Are you sure you want to revoke your API key? This action cannot be undone.')) {
        return;
    }
    
    const errorDiv = document.getElementById('apikey-error-message');
    const successDiv = document.getElementById('apikey-success-message');
    const revokeBtn = document.getElementById('revoke-apikey-btn');
    
    errorDiv.classList.add('d-none');
    successDiv.classList.add('d-none');
    
    revokeBtn.disabled = true;
    const revokingText = window.i18n ? window.i18n.t('profile.revoking') : 'Revoking...';
    revokeBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${revokingText}`;
    
    try {
        const token = localStorage.getItem('access_token');
        const response = await fetch('/api/auth/api-key', {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            const successMsg = window.i18n ? window.i18n.t('profile.apiKeyRevoked') : 'API key revoked successfully';
            successDiv.textContent = successMsg;
            successDiv.classList.remove('d-none');
            loadApiKey();
        } else {
            errorDiv.textContent = data.detail || (window.i18n ? window.i18n.t('profile.apiKeyRevokeFailed') : 'Failed to revoke API key');
            errorDiv.classList.remove('d-none');
        }
    } catch (error) {
        const errorMsg = window.i18n ? window.i18n.t('profile.apiKeyRevokeFailed') : 'Failed to revoke API key';
        errorDiv.textContent = `${errorMsg}: ${error.message}`;
        errorDiv.classList.remove('d-none');
    } finally {
        revokeBtn.disabled = false;
        const revokeText = window.i18n ? window.i18n.t('profile.revokeApiKey') : 'Revoke';
        revokeBtn.innerHTML = `<i class="bi bi-trash"></i> ${revokeText}`;
    }
});

// Event listeners
document.getElementById('apikey-refresh-captcha').addEventListener('click', loadApiKeyCaptcha);
document.getElementById('apikey-captcha-image').addEventListener('click', loadApiKeyCaptcha);
document.getElementById('generate-apikey-btn').addEventListener('click', generateApiKey);
document.getElementById('regenerate-apikey-btn').addEventListener('click', generateApiKey);

// Load user info and CAPTCHAs on page load
loadUserInfo();
loadSteamApiKey();
loadGitHubTokenStatus();
loadS3Settings();
loadProfileCaptcha();
loadPasswordCaptcha();
loadS3Captcha();
loadApiKeyCaptcha();
loadApiKey();
loadUserAISettings().catch((error) => showProfileAIResult(error.message, false));
loadDiscordBotSettings().catch((error) => showDiscordBotResult(error.message, false));

