// Toast notification helper function
function showBatchActionToast(message, type = 'success') {
    const toastEl = document.getElementById('batchActionToast');
    const toastMessage = document.getElementById('batchActionToastMessage');
    
    if (!toastEl || !toastMessage) return;
    
    // Set message
    toastMessage.textContent = message;
    
    // Set color based on type
    toastEl.classList.remove('bg-success', 'bg-danger', 'bg-warning', 'bg-info');
    const bgClass = type === 'error' ? 'bg-danger' : 
                   type === 'warning' ? 'bg-warning' : 
                   type === 'info' ? 'bg-info' : 'bg-success';
    toastEl.classList.add(bgClass);
    
    // Update icon
    const iconEl = toastEl.querySelector('.bi');
    if (iconEl) {
        iconEl.classList.remove('bi-check-circle', 'bi-x-circle', 'bi-exclamation-triangle', 'bi-info-circle');
        const iconClass = type === 'error' ? 'bi-x-circle' : 
                         type === 'warning' ? 'bi-exclamation-triangle' : 
                         type === 'info' ? 'bi-info-circle' : 'bi-check-circle';
        iconEl.classList.add(iconClass);
    }
    
    // Get or create Bootstrap Toast instance
    let toast = bootstrap.Toast.getInstance(toastEl);
    if (!toast) {
        toast = new bootstrap.Toast(toastEl, {
            autohide: true,
            delay: 3000
        });
    }
    
    // Show toast
    toast.show();
}

// Alias for compatibility with other parts of the code
const showToast = showBatchActionToast;

function serverManager() {
    // Constants for A2S polling
    const AGGRESSIVE_POLL_INTERVAL_MS = 3000;  // 3 seconds
    const NORMAL_POLL_INTERVAL_MS = 30000;  // 30 seconds
    const DISK_SPACE_POLL_INTERVAL_MS = 1 * 60 * 60 * 1000;  // 1 hour
    
    return {
        servers: [],
        loading: true,
        error: null,
        selectedServers: [],
        bulkActionRunning: false,
        bulkProgress: 0,
        a2sData: {},  // Store A2S data keyed by server ID
        a2sLoading: false,  // Track if A2S data is being loaded
        diskSpaceData: {},  // Store disk space data keyed by server ID
        diskSpaceRefreshing: false,  // Track if disk space is being manually refreshed
        diskSpaceRefreshingServers: [],  // Track which individual servers are being refreshed
        sshHealthData: {},  // Store SSH health data keyed by server ID
        sshReconnecting: {},  // Track which servers are being reconnected
        steamLatestVersion: null,  // Store Steam latest version info
        refreshInterval: null,
        diskSpaceInterval: null,
        sshHealthInterval: null,  // Interval for SSH health polling
        pollAttempts: 0,  // Track polling attempts
        maxAggressivePolls: 20,  // Max aggressive polls (20 * 3s = 60s max)
        currentUser: null,  // Current user info
        adminViewMode: false,  // Admin view mode toggle
        
        async init() {
            // Load current user info first
            await this.loadCurrentUser();
            await this.loadServers();
            // Initial A2S data load
            await this.refreshA2SData();
            // Initial disk space data load
            await this.refreshDiskSpaceData();
            // Initial SSH health data load
            await this.refreshSSHHealthData();
            // Start with aggressive polling (every 3 seconds) until we have data
            this.startPolling();
            // Start disk space polling (every 6 hours)
            this.startDiskSpacePolling();
            // Start SSH health polling (every 5 minutes)
            this.startSSHHealthPolling();
        },
        
        async loadCurrentUser() {
            try {
                const response = await authFetch('/api/auth/me');
                if (response.ok) {
                    this.currentUser = await response.json();
                }
            } catch (err) {
                console.error('Failed to load current user:', err);
            }
        },
        
        startPolling() {
            // Clear any existing interval
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
            }
            
            // Determine polling interval based on whether we have data or max attempts reached
            const hasData = Object.keys(this.a2sData).length > 0;
            const maxAttemptsReached = this.pollAttempts >= this.maxAggressivePolls;
            const interval = (hasData || maxAttemptsReached) ? NORMAL_POLL_INTERVAL_MS : AGGRESSIVE_POLL_INTERVAL_MS;
            
            this.refreshInterval = setInterval(() => this.refreshA2SData(), interval);
            console.log(`Polling interval set to ${interval/1000} seconds (hasData: ${hasData}, attempts: ${this.pollAttempts}/${this.maxAggressivePolls})`);
        },
        
        async loadServers() {
            this.loading = true;
            this.error = null;
            try {
                // SECURITY: Client-side admin check is for UX only - server validates permissions.
                // Endpoint selection based on client state is safe because:
                // 1. Server always validates admin permission on /servers/admin/all
                // 2. Tampering with client state results in 403 error (handled below)
                // 3. Regular endpoint /servers enforces user ownership server-side
                const endpoint = this.adminViewMode && this.currentUser?.is_admin 
                    ? '/servers/admin/all' 
                    : '/servers';
                const response = await authFetch(endpoint);
                if (!response.ok) {
                    if (response.status === 403) {
                        // Permission denied - reset admin mode and reload with user endpoint
                        this.adminViewMode = false;
                        throw new Error('Access denied. You do not have admin permissions.');
                    }
                    throw new Error('Failed to load servers');
                }
                this.servers = await response.json();
            } catch (err) {
                this.error = err.message;
                // Reset admin mode on error if it was enabled
                if (this.adminViewMode && err.message.includes('Access denied')) {
                    this.adminViewMode = false;
                }
            } finally {
                this.loading = false;
            }
        },
        
        async toggleAdminView() {
            if (!this.currentUser?.is_admin) return;
            // adminViewMode is already updated by x-model binding
            await this.loadServers();
            await this.refreshA2SData();
        },
        
        async refreshA2SData() {
            // Fetch cached A2S data for all servers from backend
            this.a2sLoading = true;
            this.pollAttempts++;
            
            try {
                const endpoint = this.adminViewMode && this.currentUser?.is_admin
                    ? '/a2s-cache?admin_view=true'
                    : '/a2s-cache';
                const response = await authFetch(endpoint);
                if (response.ok) {
                    const data = await response.json();
                    const oldDataCount = Object.keys(this.a2sData).length;
                    const newDataCount = Object.keys(data.servers || {}).length;
                    
                    // Update a2sData with cached info
                    this.a2sData = data.servers || {};
                    
                    // Update Steam latest version
                    if (data.steam_latest_version) {
                        this.steamLatestVersion = data.steam_latest_version;
                        console.log('Steam latest version:', this.steamLatestVersion.version);
                    }
                    
                    console.log(`A2S data refreshed (attempt ${this.pollAttempts}): ${newDataCount} servers`);
                    console.log('A2S data structure:', this.a2sData);
                    console.log('Server IDs from servers list:', this.servers.map(s => s.id));
                    
                    // If we just got data for the first time, or hit max attempts, switch to normal polling
                    if ((oldDataCount === 0 && newDataCount > 0) || this.pollAttempts >= this.maxAggressivePolls) {
                        if (newDataCount > 0) {
                            console.log('First data received, switching to normal 30s polling interval');
                        } else {
                            console.log('Max aggressive polling attempts reached, switching to normal 30s polling interval');
                        }
                        this.startPolling();  // This will switch to 30s interval
                    }
                } else {
                    console.error('A2S cache fetch failed with status:', response.status);
                }
            } catch (err) {
                console.error('Failed to fetch A2S cache:', err);
            } finally {
                this.a2sLoading = false;
            }
        },
        
        startDiskSpacePolling() {
            // Poll disk space every 1 hour
            this.diskSpaceInterval = setInterval(() => this.refreshDiskSpaceData(), DISK_SPACE_POLL_INTERVAL_MS);
            console.log('Disk space polling started (every 1 hour)');
        },
        
        async refreshDiskSpaceData() {
            try {
                const response = await authFetch('/servers/disk-space-all');
                if (response.ok) {
                    const data = await response.json();
                    this.diskSpaceData = data.servers || {};
                    console.log('Disk space data refreshed:', Object.keys(this.diskSpaceData).length, 'servers');
                } else {
                    console.warn('Failed to fetch disk space data:', response.status);
                }
            } catch (err) {
                console.error('Error fetching disk space data:', err);
            }
        },
        
        async manualRefreshDiskSpace() {
            this.diskSpaceRefreshing = true;
            try {
                const response = await authFetch('/servers/disk-space-all?force_refresh=true');
                if (response.ok) {
                    const data = await response.json();
                    this.diskSpaceData = data.servers || {};
                    console.log('Disk space data manually refreshed:', Object.keys(this.diskSpaceData).length, 'servers');
                    showToast('success', 'Disk space refreshed successfully');
                } else {
                    console.warn('Failed to manually refresh disk space data:', response.status);
                    showToast('error', `Failed to refresh disk space (HTTP ${response.status})`);
                }
            } catch (err) {
                console.error('Error manually refreshing disk space data:', err);
                showToast('error', `Error refreshing disk space: ${err.message || 'Network error'}`);
            } finally {
                this.diskSpaceRefreshing = false;
            }
        },
        
        async refreshSingleServerDiskSpace(serverId) {
            // Add server to refreshing list
            if (this.diskSpaceRefreshingServers.includes(serverId)) {
                return; // Already refreshing
            }
            this.diskSpaceRefreshingServers.push(serverId);
            
            try {
                const response = await authFetch(`/servers/${serverId}/disk-space?force_refresh=true`);
                if (response.ok) {
                    const data = await response.json();
                    if (data.success && data.disk_space) {
                        // Update the disk space data for this server
                        this.diskSpaceData[serverId] = data.disk_space;
                        console.log(`Disk space refreshed for server ${serverId}`);
                        showToast('success', window.i18n?.t('servers.diskSpace.refreshSuccess') || 'Disk space refreshed');
                    } else {
                        showToast('error', window.i18n?.t('servers.diskSpace.refreshFailed') || 'Failed to get disk space');
                    }
                } else {
                    console.warn(`Failed to refresh disk space for server ${serverId}:`, response.status);
                    showToast('error', `Failed to refresh (HTTP ${response.status})`);
                }
            } catch (err) {
                console.error(`Error refreshing disk space for server ${serverId}:`, err);
                showToast('error', `Error: ${err.message || 'Network error'}`);
            } finally {
                // Remove server from refreshing list
                this.diskSpaceRefreshingServers = this.diskSpaceRefreshingServers.filter(id => id !== serverId);
            }
        },
        
        getDiskSpace(serverId) {
            return this.diskSpaceData[serverId] || null;
        },
        
        formatDiskSpace(gb) {
            if (gb === null || gb === undefined) return 'N/A';
            return gb.toFixed(2) + ' GB';
        },
        
        async refreshSSHHealthData() {
            // Fetch SSH health data for all servers
            try {
                const promises = this.servers.map(async server => {
                    try {
                        const response = await authFetch(`/servers/${server.id}/ssh-health`);
                        if (response.ok) {
                            const data = await response.json();
                            this.sshHealthData[server.id] = data;
                        }
                    } catch (err) {
                        console.error(`Failed to fetch SSH health for server ${server.id}:`, err);
                    }
                });
                await Promise.all(promises);
            } catch (err) {
                console.error('Error refreshing SSH health data:', err);
            }
        },
        
        startSSHHealthPolling() {
            // Poll SSH health every 5 minutes
            this.sshHealthInterval = setInterval(() => this.refreshSSHHealthData(), 5 * 60 * 1000);
        },
        
        getSSHHealth(serverId) {
            return this.sshHealthData[serverId] || null;
        },
        
        async manualSSHReconnect(serverId) {
            this.sshReconnecting[serverId] = true;
            try {
                const response = await authFetch(`/servers/${serverId}/ssh-reconnect`, {
                    method: 'POST'
                });
                
                if (response.ok) {
                    const result = await response.json();
                    if (result.success) {
                        // Refresh SSH health data for this server
                        const healthResponse = await authFetch(`/servers/${serverId}/ssh-health`);
                        if (healthResponse.ok) {
                            const data = await healthResponse.json();
                            this.sshHealthData[serverId] = data;
                        }
                        showToast('success', 'SSH reconnection successful!');
                    } else {
                        showToast('error', `Reconnection failed: ${result.message}`);
                    }
                } else {
                    showToast('error', 'Failed to reconnect to server');
                }
            } catch (err) {
                console.error('Manual SSH reconnect failed:', err);
                showToast('error', 'Failed to reconnect to server');
            } finally {
                this.sshReconnecting[serverId] = false;
            }
        },
        
        getA2SInfo(serverId) {
            // Convert serverId to string to match API response keys
            const key = String(serverId);
            const info = this.a2sData[key] || null;
            if (!info) {
                console.log(`No A2S info found for server ${serverId} (key: ${key})`);
                console.log('Available keys:', Object.keys(this.a2sData));
            }
            return info;
        },
        
        formatDate(dateString) {
            if (!dateString) return 'N/A';
            return new Date(dateString).toLocaleDateString();
        },
        
        formatTimestamp(timestamp) {
            if (!timestamp) return 'N/A';
            try {
                const date = new Date(timestamp);
                const now = new Date();
                const diffMs = now - date;
                const diffSec = Math.floor(diffMs / 1000);
                
                // Handle invalid dates
                if (isNaN(date.getTime())) {
                    return 'N/A';
                }
                
                // If timestamp is in the future (timezone issues), show absolute time
                if (diffSec < 0) {
                    return date.toLocaleString();
                }
                
                // Show relative time if recent
                if (diffSec < 60) {
                    return `${diffSec}s ago`;
                } else if (diffSec < 3600) {
                    const mins = Math.floor(diffSec / 60);
                    return `${mins}m ago`;
                } else if (diffSec < 86400) {
                    const hours = Math.floor(diffSec / 3600);
                    return `${hours}h ago`;
                } else {
                    // Show full date/time if older than a day
                    return date.toLocaleString();
                }
            } catch (e) {
                return 'N/A';
            }
        },
        
        parseVersion(versionStr) {
            if (!versionStr) return null;
            // Extract version like "1.41.2.5" from "1.41.2.5/14125"
            const match = versionStr.match(/(\d+\.\d+\.\d+\.\d+)/);
            return match ? match[1] : versionStr;
        },
        
        isVersionOutdated(serverVersion) {
            if (!serverVersion || !this.steamLatestVersion) return false;
            const parsedServerVersion = this.parseVersion(serverVersion);
            const steamVersion = this.steamLatestVersion.version;
            return parsedServerVersion && steamVersion && parsedServerVersion !== steamVersion;
        },
        
        isVersionUpToDate(serverVersion) {
            if (!serverVersion || !this.steamLatestVersion) return false;
            const parsedServerVersion = this.parseVersion(serverVersion);
            const steamVersion = this.steamLatestVersion.version;
            return parsedServerVersion && steamVersion && parsedServerVersion === steamVersion;
        },
        
        isSelected(serverId) {
            return this.selectedServers.includes(serverId);
        },
        
        toggleServer(serverId) {
            const index = this.selectedServers.indexOf(serverId);
            if (index === -1) {
                this.selectedServers.push(serverId);
            } else {
                this.selectedServers.splice(index, 1);
            }
        },
        
        toggleSelectAll() {
            if (this.selectedServers.length === this.servers.length) {
                this.selectedServers = [];
            } else {
                this.selectedServers = this.servers.map(s => s.id);
            }
        },
        
        clearSelection() {
            this.selectedServers = [];
        },

        openImportModal() {
            const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('importServerConfigModal'));
            modal.show();
        },

        async exportServers(includeSecrets, serverIds = this.servers.map(server => server.id)) {
            if (!serverIds || serverIds.length === 0) {
                showWarning(window.i18n?.t('servers.bulkActions.selectServer') || 'No servers available to export');
                return;
            }

            const params = new URLSearchParams();
            serverIds.forEach(serverId => params.append('server_ids', serverId));
            params.set('include_secrets', includeSecrets ? 'true' : 'false');

            try {
                const response = await authFetch(`/servers/export?${params.toString()}`);
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to export server configuration');
                }

                const blob = await response.blob();
                const contentDisposition = response.headers.get('Content-Disposition') || '';
                const filenameMatch = contentDisposition.match(/filename=([^;]+)/i);
                const filename = filenameMatch ? decodeURIComponent(filenameMatch[1].replace(/"/g, '')) : 'cs2-server-config.json';
                const downloadUrl = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = downloadUrl;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);

                const messageKey = includeSecrets ? 'servers.exportSuccess' : 'servers.exportRedactedSuccess';
                showBatchActionToast(window.i18n?.t(messageKey, { count: serverIds.length }) || 'Configuration exported');
            } catch (err) {
                showError((window.i18n?.t('errors.exportingServerConfig') || 'Error exporting server configuration') + ': ' + err.message);
            }
        },

        async importServers() {
            const fileInput = document.getElementById('serverConfigFile');
            const file = fileInput?.files?.[0];
            if (!file) {
                showWarning(window.i18n?.t('servers.selectConfigFile') || 'Please select a configuration file');
                return;
            }

            const importButton = document.getElementById('importServerConfigBtn');
            const resultElement = document.getElementById('serverConfigImportResult');
            importButton.disabled = true;
            resultElement.textContent = window.i18n?.t('servers.importing') || 'Importing...';

            try {
                const payload = JSON.parse(await file.text());
                payload.conflict_strategy = document.getElementById('serverConfigConflictStrategy').value;
                const response = await authFetch('/servers/import', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to import server configuration');
                }

                const result = await response.json();
                resultElement.textContent = window.i18n?.t('servers.importSummary', {
                    imported: result.imported,
                    updated: result.updated,
                    skipped: result.skipped,
                    failed: result.failed
                }) || `Imported ${result.imported}, updated ${result.updated}, skipped ${result.skipped}, failed ${result.failed}`;
                await this.loadServers();
                this.clearSelection();
                showBatchActionToast(resultElement.textContent, result.failed > 0 ? 'warning' : 'success');
            } catch (err) {
                resultElement.textContent = (window.i18n?.t('errors.importingServerConfig') || 'Error importing server configuration') + ': ' + err.message;
                showError(resultElement.textContent);
            } finally {
                importButton.disabled = false;
            }
        },
        
        async bulkAction(action) {
            if (this.selectedServers.length === 0) {
                const selectServerMsg = window.i18n?.t('servers.bulkActions.selectServer') || 'Please select at least one server';
                showWarning(selectServerMsg);
                return;
            }
            
            const actionName = action.charAt(0).toUpperCase() + action.slice(1);
            
            // Use confirm dialog with i18n support
            const message = window.i18n?.t('confirmMessages.bulkAction', {
                action: action,
                count: this.selectedServers.length
            }) || `Are you sure you want to ${action} ${this.selectedServers.length} server(s)?`;
            
            showConfirm(
                message,
                () => { this.executeBulkAction(action, actionName); },
                null
            );
        },
        
        async executeBulkAction(action, actionName) {
            this.bulkActionRunning = true;
            this.bulkProgress = 0;
            
            const total = this.selectedServers.length;
            
            try {
                // Use the new async batch-actions endpoint - returns immediately
                const response = await authFetch('/servers/batch-actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        server_ids: this.selectedServers,
                        action: action
                    })
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to start batch action');
                }
                
                const result = await response.json();
                const batchId = result.batch_id;
                
                // Show dispatch notification as toast - command has been sent
                const dispatchMsg = window.i18n?.t('servers.batchActions.dispatched', {
                    count: total
                }) || `Command dispatched to ${total} server(s)`;
                showBatchActionToast(dispatchMsg, 'success');
                
                // Clear selection immediately - UI is not blocked
                this.clearSelection();
                
                // Poll for completion and show final result
                this.pollBatchActionResult(batchId, actionName);
                
            } catch (err) {
                const errorPrefix = window.i18n?.t('errors.startingBatchAction') || 'Error starting batch action';
                showError(errorPrefix + ': ' + err.message);
            } finally {
                this.bulkActionRunning = false;
                this.bulkProgress = 0;
            }
        },
        
        async pollBatchActionResult(batchId, actionName) {
            const maxPolls = 60; // Max 2 minutes (60 * 2s)
            const pollInterval = 2000; // 2 seconds
            let pollCount = 0;
            
            const poll = async () => {
                try {
                    const response = await authFetch(`/servers/batch-actions/${batchId}`);
                    if (!response.ok) {
                        console.error('Failed to fetch batch action status');
                        return;
                    }
                    
                    const status = await response.json();
                    const summary = status.summary;
                    
                    if (!summary.is_complete && pollCount < maxPolls) {
                        pollCount++;
                        setTimeout(poll, pollInterval);
                    } else {
                        // Show final result as toast
                        const failedServers = Object.entries(status.servers)
                            .filter(([_, s]) => s.status === 'failed')
                            .map(([id, s]) => {
                                const server = this.servers.find(srv => srv.id == id);
                                return server ? server.name : `Server ${id}`;
                            });
                        
                        let resultMsg = window.i18n?.t('servers.batchActions.completed', {
                            action: actionName,
                            succeeded: summary.succeeded,
                            failed: summary.failed
                        }) || `${actionName} completed: ${summary.succeeded} succeeded, ${summary.failed} failed`;
                        
                        if (failedServers.length > 0) {
                            const failedListLabel = window.i18n?.t('servers.batchActions.failedServersList') || 'Failed servers';
                            resultMsg += ` | ${failedListLabel}: ${failedServers.join(', ')}`;
                        }
                        
                        if (summary.failed > 0) {
                            showBatchActionToast(resultMsg, 'warning');
                        } else {
                            showBatchActionToast(resultMsg, 'success');
                        }
                        
                        // Reload servers to get updated status
                        await this.loadServers();
                    }
                } catch (err) {
                    console.error('Error polling batch action status:', err);
                }
            };
            
            setTimeout(poll, pollInterval);
        },
        
        async bulkInstallPlugins() {
            if (this.selectedServers.length === 0) {
                const selectServerMsg = window.i18n?.t('servers.bulkActions.selectServer') || 'Please select at least one server';
                showWarning(selectServerMsg);
                return;
            }
            
            // Show plugin selection dialog
            const pluginSelection = await this.showPluginSelectionDialog();
            if (!pluginSelection || pluginSelection.length === 0) {
                return;
            }
            
            // Confirm installation
            const confirmMsg = window.i18n?.t('confirmMessages.install', {
                plugins: pluginSelection.join(', '),
                count: this.selectedServers.length
            }) || `Install ${pluginSelection.join(', ')} on ${this.selectedServers.length} server(s)?`;
            showConfirm(
                confirmMsg,
                () => { this.executeBulkInstallPlugins(pluginSelection); },
                null
            );
        },
        
        async executeBulkInstallPlugins(pluginSelection) {
            this.bulkActionRunning = true;
            this.bulkProgress = 0;
            
            const total = this.selectedServers.length;
            
            try {
                // Use the new async batch-install-plugins endpoint - returns immediately
                const response = await authFetch('/servers/batch-install-plugins', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        server_ids: this.selectedServers,
                        plugins: pluginSelection
                    })
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to start plugin installation');
                }
                
                const result = await response.json();
                const batchId = result.batch_id;
                
                // Show dispatch notification as toast - command has been sent
                const dispatchMsg = window.i18n?.t('servers.batchActions.dispatched', {
                    count: total
                }) || `Command dispatched to ${total} server(s)`;
                showBatchActionToast(dispatchMsg, 'success');
                
                // Clear selection immediately - UI is not blocked
                this.clearSelection();
                
                // Poll for completion and show final result
                this.pollBatchActionResult(batchId, window.i18n?.t('servers.batchActions.installPlugins') || 'Install Plugins');
                
            } catch (err) {
                let errorMsg = 'Unknown error';
                if (err.message) {
                    errorMsg = err.message;
                } else if (err.toString && typeof err.toString === 'function') {
                    errorMsg = err.toString();
                } else {
                    errorMsg = String(err);
                }
                const errorPrefix = window.i18n?.t('errors.installingPlugins') || 'Error installing plugins';
                showError(errorPrefix + ': ' + errorMsg);
            } finally {
                this.bulkActionRunning = false;
                this.bulkProgress = 0;
            }
        },
        
        async bulkSendCommand() {
            if (this.selectedServers.length === 0) {
                const selectServerMsg = window.i18n?.t('servers.bulkActions.selectServer') || 'Please select at least one server';
                showWarning(selectServerMsg);
                return;
            }
            
            // Show command input modal
            const modal = new bootstrap.Modal(document.getElementById('sendCommandModal'));
            modal.show();
        },
        
        async executeBulkSendCommand(command) {
            this.bulkActionRunning = true;
            this.bulkProgress = 0;
            
            const total = this.selectedServers.length;
            
            try {
                // Use the new async batch-send-command endpoint - returns immediately
                const response = await authFetch('/servers/batch-send-command', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        server_ids: this.selectedServers,
                        command: command
                    })
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to send command');
                }
                
                const result = await response.json();
                const batchId = result.batch_id;
                
                // Show dispatch notification
                const dispatchMsg = window.i18n?.t('servers.bulkActions.commandDispatch', {
                    count: total,
                    command: command
                }) || `Command sent to ${total} server(s): ${command}`;
                showBatchActionToast(dispatchMsg, 'success');
                
                // Clear selection immediately
                this.clearSelection();
                
                // Poll for completion
                this.pollBatchActionResult(batchId, 'Send Command');
                
            } catch (err) {
                let errorMsg = 'Unknown error';
                if (err.message) {
                    errorMsg = err.message;
                } else if (err.toString && typeof err.toString === 'function') {
                    errorMsg = err.toString();
                } else {
                    errorMsg = String(err);
                }
                const errorPrefix = window.i18n?.t('errors.sendingCommand') || 'Error sending command';
                showError(errorPrefix + ': ' + errorMsg);
            } finally {
                this.bulkActionRunning = false;
                this.bulkProgress = 0;
            }
        },
        
        async showPluginSelectionDialog() {
            return new Promise((resolve) => {
                const plugins = [];
                
                // First ask about Metamod
                const metamodMsg = window.i18n?.t('confirmMessages.installMetamod') || 'Install Metamod:Source?\n\n(Required for most CS2 plugins)';
                const cssMsg = window.i18n?.t('confirmMessages.installCSS') || 'Install CounterStrikeSharp?\n\n(Write server plugins in C#. Requires Metamod)';
                
                showConfirm(
                    metamodMsg,
                    () => {
                        plugins.push('metamod');
                        // Then ask about CounterStrikeSharp
                        showConfirm(
                            cssMsg,
                            () => {
                                plugins.push('counterstrikesharp');
                                resolve(plugins);
                            },
                            () => {
                                resolve(plugins);
                            }
                        );
                    },
                    () => {
                        // If Metamod not selected, still ask about CounterStrikeSharp
                        showConfirm(
                            cssMsg,
                            () => {
                                plugins.push('counterstrikesharp');
                                resolve(plugins);
                            },
                            () => {
                                resolve(plugins);
                            }
                        );
                    }
                );
            });
        },
        
        async deleteServer(id, name) {
            const message = window.i18n?.t('confirmMessages.deleteServer', {
                serverName: name
            }) || `Are you sure you want to delete server "${name}"?`;
            
            showConfirm(
                message,
                async () => {
                    try {
                        const response = await authFetch(`/servers/${id}`, {
                            method: 'DELETE'
                        });
                        if (!response.ok) throw new Error('Failed to delete server');
                        await this.loadServers();
                    } catch (err) {
                        const errorPrefix = window.i18n?.t('errors.deletingServer') || 'Error deleting server';
                        showError(errorPrefix + ': ' + err.message);
                    }
                },
                null
            );
        },
        
        destroy() {
            // Clean up refresh interval
            if (this.refreshInterval) {
                clearInterval(this.refreshInterval);
                this.refreshInterval = null;
            }
            // Clean up disk space interval
            if (this.diskSpaceInterval) {
                clearInterval(this.diskSpaceInterval);
                this.diskSpaceInterval = null;
            }
        }
    };
}

// Confirm and send command to servers
function confirmSendCommand() {
    const commandInput = document.getElementById('commandInput');
    const command = commandInput.value.trim();
    
    if (!command) {
        showWarning(window.i18n?.t('servers.sendCommand.emptyCommand') || 'Please enter a command');
        return;
    }
    
    // Close modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('sendCommandModal'));
    if (modal) {
        modal.hide();
    }
    
    // Get Alpine.js component instance
    const app = Alpine.$data(document.querySelector('[x-data]'));
    if (app && app.executeBulkSendCommand) {
        app.executeBulkSendCommand(command);
    }
    
    // Clear input for next time
    commandInput.value = '';
}

function confirmImportServers() {
    const app = Alpine.$data(document.querySelector('[x-data]'));
    if (app && app.importServers) {
        app.importServers();
    }
}

function toggleAuthFields() {
    // Only password authentication is supported for quick-fill servers
    // Auth type is always 'password', so just ensure password field is visible
    const passwordField = document.getElementById('passwordField');
    if (passwordField) {
        passwordField.style.display = 'block';
        document.getElementById('sshPassword').required = true;
    }
}

// Load CAPTCHA for server form
async function loadServerCaptcha() {
    try {
        const response = await fetch('/api/captcha/image/new');
        const token = response.headers.get('X-Captcha-Token');
        const blob = await response.blob();
        const imageUrl = URL.createObjectURL(blob);
        
        document.getElementById('server-captcha-image').src = imageUrl;
        document.getElementById('server-captcha-token').value = token;
    } catch (error) {
        console.error('Failed to load server CAPTCHA:', error);
    }
}

// Function to update game directory base path based on SSH user
function updateGameDirectoryBase() {
    const sshUser = document.getElementById('sshUser').value || 'cs2server';
    const basePathElement = document.getElementById('gameDirectoryBase');
    if (basePathElement) {
        basePathElement.textContent = `/home/${sshUser}/`;
    }
}

// Refresh CAPTCHA for server form
document.getElementById('refresh-server-captcha')?.addEventListener('click', loadServerCaptcha);
document.getElementById('server-captcha-image')?.addEventListener('click', loadServerCaptcha);

// Update game directory base path when SSH user changes
document.getElementById('sshUser')?.addEventListener('input', updateGameDirectoryBase);

// Load CAPTCHA and initialized servers when modal is shown
document.getElementById('addServerModal')?.addEventListener('shown.bs.modal', () => {
    loadServerCaptcha();
    loadInitializedServers();
    updateGameDirectoryBase(); // Update base path on modal show
});

async function submitAddServer() {
    const form = document.getElementById('addServerForm');
    if (!form.checkValidity()) {
        form.reportValidity();
        return;
    }
    
    const progressDiv = document.getElementById('addServerProgress');
    const progressText = document.getElementById('addServerProgressText');
    const submitBtn = document.getElementById('submitAddServerBtn');
    
    // Show progress and disable button
    progressDiv.style.display = 'block';
    submitBtn.disabled = true;
    
    const updateProgress = (message) => {
        progressText.textContent = message;
    };
    
    // Combine base path with directory name
    const sshUser = document.getElementById('sshUser').value;
    const directoryName = document.getElementById('gameDirectoryName').value;
    const gameDirectory = `/home/${sshUser}/${directoryName}`;
    
    const data = {
        name: document.getElementById('serverName').value,
        host: document.getElementById('serverHost').value,
        ssh_port: parseInt(document.getElementById('sshPort').value),
        ssh_user: sshUser,
        ssh_password: document.getElementById('sshPassword').value,
        game_port: parseInt(document.getElementById('gamePort').value),
        game_directory: gameDirectory,
        session_manager: document.getElementById('sessionManager').value || 'tmux',
        description: document.getElementById('serverDescription').value,
        captcha_token: document.getElementById('server-captcha-token').value,
        captcha_code: document.getElementById('serverCaptcha').value
    };
    
    try {
        updateProgress('Validating...');
        updateProgress('Connecting to server...');
        updateProgress('Verifying credentials...');
        
        const response = await authFetch('/servers', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create server');
        }
        
        updateProgress('Server added successfully!');
        await new Promise(resolve => setTimeout(resolve, 300)); // Brief delay to show success message
        
        // Close modal and reload
        const modal = bootstrap.Modal.getInstance(document.getElementById('addServerModal'));
        modal.hide();
        form.reset();
        
        // Reload servers
        const app = Alpine.$data(document.querySelector('[x-data]'));
        await app.loadServers();
    } catch (err) {
        const prefix = window.i18n?.t('errors.errorCreatingServer') || 'Error creating server';
        showError(`${prefix}: ${err.message}`);
        // Refresh CAPTCHA on error
        loadServerCaptcha();
    } finally {
        // Hide progress and re-enable button
        progressDiv.style.display = 'none';
        submitBtn.disabled = false;
    }
}

// Load initialized servers when modal is shown
async function loadInitializedServers() {
    try {
        const response = await authFetch('/api/setup/initialized-servers');
        if (response.ok) {
            const servers = await response.json();
            const select = document.getElementById('initializedServerSelect');
            const noServersMsg = document.getElementById('noInitializedServersMsg');
            
            // Clear existing options (except the first one)
            while (select.options.length > 1) {
                select.remove(1);
            }
            
            if (servers.length > 0) {
                servers.forEach(server => {
                    const option = document.createElement('option');
                    option.value = server.key;  // Store Redis key
                    option.textContent = `${server.name} (${server.host})`;
                    select.appendChild(option);
                });
                select.style.display = 'block';
                noServersMsg.style.display = 'none';
            } else {
                select.style.display = 'none';
                noServersMsg.style.display = 'block';
            }
        }
    } catch (error) {
        console.error('Failed to load initialized servers:', error);
    }
}

async function fillFromInitializedServer(serverKey) {
    if (!serverKey) return;
    
    try {
        // Fetch full server details including credentials using Redis key
        const response = await authFetch(`/api/setup/initialized-servers/${encodeURIComponent(serverKey)}`);
        if (!response.ok) {
            throw new Error('Failed to fetch server details');
        }
        
        const server = await response.json();
        
        // Initialized servers always use password authentication
        toggleAuthFields(); // Ensure password field is visible
        
        // Fill in server details
        document.getElementById('serverName').value = server.name;
        document.getElementById('serverHost').value = server.host;
        document.getElementById('sshPort').value = server.ssh_port;
        document.getElementById('sshUser').value = server.ssh_user;
        document.getElementById('sshPassword').value = server.ssh_password;
        
        // Extract directory name from full path (e.g., /home/cs2server/cs2 -> cs2)
        const gameDir = server.game_directory || '/home/cs2server/cs2';
        const parts = gameDir.replace(/\/+$/, '').split('/').filter(Boolean); // Remove trailing slashes and empty parts
        const directoryName = parts.length > 0 ? parts[parts.length - 1] : 'cs2';
        document.getElementById('gameDirectoryName').value = directoryName;
        
        // Update base path display
        updateGameDirectoryBase();
    } catch (error) {
        console.error('Error filling server details:', error);
        const errorPrefix = window.i18n?.t('errors.loadingServerDetails') || 'Error loading server details';
        showError(errorPrefix + ': ' + error.message);
    }
}

