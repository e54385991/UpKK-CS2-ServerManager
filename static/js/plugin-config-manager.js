(function () {
    function translated(key, fallback) {
        const value = window.i18n?.t(key);
        return value && value !== key ? value : (fallback || key);
    }

    window.pluginConfigManager = function (serverId) {
        return {
            serverId,
            initialized: false,
            loadingSources: false,
            busy: false,
            sources: [],
            gameDirectory: '',
            activeSourceId: null,
            showAddSource: false,
            addingSource: false,
            sourcePath: '',
            showBrowser: false,
            browsing: false,
            browsePath: '.',
            browseItems: [],
            fileSearch: '',
            fieldSearch: '',
            selectedFile: null,
            fileData: null,
            loadingFile: false,
            savingFile: false,
            editMode: 'visual',
            fieldValues: {},
            originalFieldValues: {},
            rawContent: '',
            originalRawContent: '',
            beforeUnloadHandler: null,

            t(key) { return translated(key, key); },

            async request(url, options = {}) {
                const response = await fetch(url, {
                    ...options,
                    headers: {
                        'Authorization': `Bearer ${localStorage.getItem('access_token')}`,
                        ...(options.body ? {'Content-Type': 'application/json'} : {}),
                        ...(options.headers || {})
                    }
                });
                let data = null;
                try { data = await response.json(); } catch (_) { data = {}; }
                if (!response.ok) {
                    const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
                    error.status = response.status;
                    throw error;
                }
                return data;
            },

            prepareSource(source, previous = null) {
                return {
                    ...(previous || {}),
                    ...source,
                    loaded: previous?.loaded || false,
                    loading: false,
                    files: previous?.files || [],
                    fileCount: previous?.fileCount || 0,
                    truncated: previous?.truncated || false,
                    scanPath: ''
                };
            },

            async reloadSources(preferredId = null) {
                const data = await this.request(`/servers/${this.serverId}/plugin-configs/sources`);
                this.gameDirectory = data.game_directory;
                const previous = new Map(this.sources.map(source => [source.id, source]));
                this.sources = data.sources.map(source => this.prepareSource(source, previous.get(source.id)));
                const preferred = this.sources.find(source => source.id === preferredId);
                const current = this.sources.find(source => source.id === this.activeSourceId);
                const nextSourceId = preferred?.id || current?.id || this.sources[0]?.id || null;
                if (nextSourceId !== this.activeSourceId) {
                    this.activeSourceId = nextSourceId;
                    this.clearEditor();
                }
                return this.sources;
            },

            async open() {
                if (this.initialized) return;
                this.initialized = true;
                this.beforeUnloadHandler = event => {
                    if (!this.dirty) return;
                    event.preventDefault();
                    event.returnValue = '';
                };
                window.addEventListener('beforeunload', this.beforeUnloadHandler);
                this.loadingSources = true;
                try {
                    await this.reloadSources();
                } catch (error) {
                    showError(`${translated('pluginConfigs.loadSourcesFailed', 'Failed to load configuration sources')}: ${error.message}`);
                } finally {
                    this.loadingSources = false;
                }
            },

            get activeSource() {
                return this.sources.find(source => source.id === this.activeSourceId) || null;
            },

            get activeFiles() {
                return this.activeSource?.files || [];
            },

            get fileGroups() {
                const search = this.fileSearch.trim().toLowerCase();
                const groups = new Map();
                for (const file of this.activeFiles) {
                    if (search && !file.tree_path.toLowerCase().includes(search)) continue;
                    const slash = file.tree_path.lastIndexOf('/');
                    const folder = slash >= 0 ? file.tree_path.slice(0, slash) : '';
                    if (!folder) {
                        if (!groups.has('')) groups.set('', []);
                    } else {
                        const parts = folder.split('/');
                        for (let index = 1; index <= parts.length; index += 1) {
                            const ancestor = parts.slice(0, index).join('/');
                            if (!groups.has(ancestor)) groups.set(ancestor, []);
                        }
                    }
                    groups.get(folder).push(file);
                }
                return Array.from(groups.entries())
                    .sort(([left], [right]) => left.localeCompare(right))
                    .map(([path, files]) => ({
                        path,
                        name: path ? path.split('/').pop() : translated('pluginConfigs.rootFolder', 'Root'),
                        depth: path ? path.split('/').length - 1 : 0,
                        files
                    }));
            },

            get fieldGroups() {
                if (!this.fileData) return [];
                const search = this.fieldSearch.trim().toLowerCase();
                const groups = new Map();
                for (const field of this.fileData.fields || []) {
                    if (search && !`${field.group} ${field.key} ${field.comment || ''}`.toLowerCase().includes(search)) continue;
                    if (!groups.has(field.group)) groups.set(field.group, []);
                    groups.get(field.group).push(field);
                }
                return Array.from(groups.entries()).map(([name, fields]) => ({name, fields}));
            },

            get dirty() {
                if (!this.fileData) return false;
                return this.editMode === 'raw'
                    ? this.rawContent !== this.originalRawContent
                    : JSON.stringify(this.fieldValues) !== JSON.stringify(this.originalFieldValues);
            },

            confirmDiscard() {
                return !this.dirty || window.confirm(translated('pluginConfigs.discardConfirm', 'Discard unsaved changes?'));
            },

            selectSource(source) {
                if (this.activeSourceId === source.id || !this.confirmDiscard()) return;
                this.activeSourceId = source.id;
                this.clearEditor();
            },

            async loadSource(source) {
                if (!this.confirmDiscard()) return;
                source.loading = true;
                source.loaded = true;
                source.files = [];
                source.fileCount = 0;
                source.truncated = false;
                source.scanPath = '.';
                this.activeSourceId = source.id;
                this.clearEditor();
                try {
                    const response = await fetch(`/servers/${this.serverId}/plugin-configs/sources/${source.id}/scan`, {
                        method: 'POST',
                        headers: {'Authorization': `Bearer ${localStorage.getItem('access_token')}`}
                    });
                    if (!response.ok) {
                        let detail = await response.text();
                        try { detail = JSON.parse(detail).detail || detail; } catch (_) { /* keep text */ }
                        throw new Error(detail);
                    }
                    if (!response.body) throw new Error(translated('pluginConfigs.streamUnavailable', 'Streaming response is unavailable'));

                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    let completed = false;
                    const handleEvent = event => {
                        if (event.type === 'progress') {
                            source.scanPath = event.directory;
                            source.fileCount = event.count;
                        } else if (event.type === 'file') {
                            source.files.push(event.file);
                            source.fileCount = source.files.length;
                        } else if (event.type === 'complete') {
                            source.files.sort((left, right) => left.tree_path.localeCompare(right.tree_path));
                            source.fileCount = event.count;
                            source.truncated = event.truncated;
                            source.scanPath = '';
                            completed = true;
                        } else if (event.type === 'error') {
                            throw new Error(event.detail || translated('pluginConfigs.scanFailed', 'Failed to scan configuration source'));
                        }
                    };
                    while (true) {
                        const {value, done} = await reader.read();
                        buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                        let newline;
                        while ((newline = buffer.indexOf('\n')) >= 0) {
                            const line = buffer.slice(0, newline).trim();
                            buffer = buffer.slice(newline + 1);
                            if (line) handleEvent(JSON.parse(line));
                        }
                        if (done) break;
                    }
                    if (buffer.trim()) handleEvent(JSON.parse(buffer));
                    if (!completed) throw new Error(translated('pluginConfigs.streamInterrupted', 'Scan stream ended unexpectedly'));
                } catch (error) {
                    source.loaded = source.files.length > 0;
                    showError(`${translated('pluginConfigs.scanFailed', 'Failed to scan configuration source')}: ${error.message}`);
                } finally {
                    source.loading = false;
                    source.scanPath = '';
                }
            },

            async addSource() {
                if (!this.sourcePath.trim()) return;
                this.addingSource = true;
                try {
                    const source = await this.request(`/servers/${this.serverId}/plugin-configs/sources`, {
                        method: 'POST', body: JSON.stringify({path: this.sourcePath.trim()})
                    });
                    const persistedSources = await this.reloadSources(source.id);
                    if (!persistedSources.some(item => item.id === source.id && item.persisted)) {
                        throw new Error(translated('pluginConfigs.persistenceFailed', 'The source was not found after saving'));
                    }
                    this.sourcePath = '';
                    this.showAddSource = false;
                    this.showBrowser = false;
                    showSuccess(translated('pluginConfigs.sourceAdded', 'Configuration source added'));
                } catch (error) {
                    showError(`${translated('pluginConfigs.addSourceFailed', 'Failed to add source')}: ${error.message}`);
                } finally {
                    this.addingSource = false;
                }
            },

            async removeSource(source) {
                if (this.activeSourceId === source.id && !this.confirmDiscard()) return;
                if (!window.confirm(translated('pluginConfigs.removeConfirm', 'Remove this configuration source?'))) return;
                try {
                    await this.request(`/servers/${this.serverId}/plugin-configs/sources/${source.id}`, {method: 'DELETE'});
                    await this.reloadSources();
                    showSuccess(translated('pluginConfigs.sourceRemoved', 'Configuration source removed'));
                } catch (error) {
                    showError(`${translated('pluginConfigs.removeSourceFailed', 'Failed to remove source')}: ${error.message}`);
                }
            },

            async restoreDefault() {
                try {
                    const source = await this.request(`/servers/${this.serverId}/plugin-configs/sources/restore-default`, {method: 'POST'});
                    await this.reloadSources(source.id);
                    showSuccess(translated('pluginConfigs.defaultRestored', 'Default source restored'));
                } catch (error) {
                    showError(`${translated('pluginConfigs.restoreFailed', 'Failed to restore default source')}: ${error.message}`);
                }
            },

            async openBrowser() {
                this.showBrowser = true;
                await this.browse('.');
            },

            async browse(path) {
                this.browsing = true;
                try {
                    const data = await this.request(`/servers/${this.serverId}/plugin-configs/browse?path=${encodeURIComponent(path)}`);
                    this.browsePath = data.path;
                    this.browseItems = data.items;
                } catch (error) {
                    showError(`${translated('pluginConfigs.browseFailed', 'Failed to browse remote path')}: ${error.message}`);
                } finally {
                    this.browsing = false;
                }
            },

            browseUp() {
                if (this.browsePath === '.') return;
                const parts = this.browsePath.split('/').filter(Boolean);
                parts.pop();
                this.browse(parts.join('/') || '.');
            },

            chooseBrowsePath(path) {
                this.sourcePath = path;
                this.showBrowser = false;
            },

            async loadFile(file, force = false) {
                if (file.too_large || (!force && !this.confirmDiscard())) return;
                this.selectedFile = file;
                this.loadingFile = true;
                this.fileData = null;
                try {
                    const data = await this.request(`/servers/${this.serverId}/plugin-configs/sources/${this.activeSourceId}/file?path=${encodeURIComponent(file.path)}`);
                    this.applyFileData(data);
                } catch (error) {
                    this.selectedFile = null;
                    showError(`${translated('pluginConfigs.loadFileFailed', 'Failed to load configuration file')}: ${error.message}`);
                } finally {
                    this.loadingFile = false;
                }
            },

            applyFileData(data) {
                this.fileData = data;
                this.rawContent = data.content;
                this.originalRawContent = data.content;
                this.fieldValues = {};
                for (const field of data.fields || []) this.fieldValues[field.id] = field.value;
                this.originalFieldValues = JSON.parse(JSON.stringify(this.fieldValues));
                this.editMode = data.visual_supported ? 'visual' : 'raw';
            },

            clearEditor() {
                this.selectedFile = null;
                this.fileData = null;
                this.fieldValues = {};
                this.originalFieldValues = {};
                this.rawContent = '';
                this.originalRawContent = '';
            },

            setFieldValue(field, value) {
                if (field.kind === 'integer') {
                    this.fieldValues[field.id] = value === '' ? null : Number.parseInt(value, 10);
                } else if (field.kind === 'number') {
                    this.fieldValues[field.id] = value === '' ? null : Number(value);
                } else {
                    this.fieldValues[field.id] = value;
                }
            },

            switchMode(mode) {
                if (mode === this.editMode || (mode === 'visual' && !this.fileData?.visual_supported)) return;
                if (this.dirty && !window.confirm(translated('pluginConfigs.modeDiscardConfirm', 'Switching modes discards unsaved changes. Continue?'))) return;
                if (mode === 'raw') {
                    this.rawContent = this.originalRawContent;
                } else {
                    this.fieldValues = JSON.parse(JSON.stringify(this.originalFieldValues));
                }
                this.editMode = mode;
            },

            async reloadFile() {
                if (!this.selectedFile || !this.confirmDiscard()) return;
                await this.loadFile(this.selectedFile, true);
            },

            async saveFile() {
                if (!this.fileData || !this.dirty) return;
                this.savingFile = true;
                const payload = {
                    path: this.fileData.path,
                    expected_revision: this.fileData.revision,
                    mode: this.editMode,
                    changes: [],
                    content: null
                };
                if (this.editMode === 'raw') {
                    payload.content = this.rawContent;
                } else {
                    payload.changes = (this.fileData.fields || [])
                        .filter(field => JSON.stringify(this.fieldValues[field.id]) !== JSON.stringify(this.originalFieldValues[field.id]))
                        .map(field => ({id: field.id, value: this.fieldValues[field.id]}));
                }
                try {
                    const data = await this.request(`/servers/${this.serverId}/plugin-configs/sources/${this.activeSourceId}/file`, {
                        method: 'PUT', body: JSON.stringify(payload)
                    });
                    this.applyFileData(data);
                    showSuccess(translated('pluginConfigs.saved', 'Configuration saved. Reload the plugin or restart the server if required.'));
                } catch (error) {
                    const prefix = error.status === 409
                        ? translated('pluginConfigs.conflict', 'The remote file changed. Reload before saving.')
                        : translated('pluginConfigs.saveFailed', 'Failed to save configuration');
                    showError(`${prefix}: ${error.message}`);
                } finally {
                    this.savingFile = false;
                }
            },

            formatFileSize(bytes) {
                if (!bytes) return '0 B';
                const units = ['B', 'KiB', 'MiB'];
                const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
                return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
            },

            formatTimestamp(timestamp) {
                return timestamp ? new Date(timestamp * 1000).toLocaleString() : '-';
            }
        };
    };
})();
