function mapManagement(serverId) {
    return {
        serverId,
        loaded: false,
        loading: false,
        saving: false,
        adding: false,
        presetApplying: '',
        customSyncLoading: false,
        customSyncSaving: false,
        customSyncRunning: false,
        uninstalling: false,
        pluginConfigLoading: false,
        pluginConfigSaving: false,
        mapActionKey: '',
        mapActionType: '',
        error: '',
        pluginConfigError: '',
        status: {
            counterstrikesharp_installed: false,
            mapchooser_installed: false,
            maps_file_exists: false,
            ready: false,
            plugin_center_name: 'CS2-Upkk-PanelPLG-Mapchooser',
            plugin_center_url: '/plugin-market?search=CS2-Upkk-PanelPLG-Mapchooser',
            maps_path: '',
            plugin_config_path: '',
            mapchooser_plugin_path: ''
        },
        maps: [],
        searchQuery: '',
        content: '',
        revision: null,
        pluginConfig: {
            fields: [],
            values: {},
            revision: null,
            file_exists: false,
            unsupported_fields: []
        },
        addForm: {
            workshop_id: '',
            name: '',
            enabled: true,
            min_players: 0,
            only_nominate: false,
            restricted_times: ''
        },
        customSync: {
            url: '',
            enabled: false,
            interval_seconds: 3600,
            last_run: null,
            next_run: null,
            last_status: null,
            last_error: null,
            run_count: 0
        },

        t(key, fallback, params = {}) {
            const translated = window.i18n?.t(key, params);
            return translated && translated !== key ? translated : fallback;
        },

        errorMessage(data, fallback) {
            if (typeof data?.detail === 'string') return data.detail;
            if (typeof data?.detail?.message === 'string') return data.detail.message;
            if (typeof data?.message === 'string') return data.message;
            return fallback;
        },

        notify(message, type = 'success') {
            const toastElement = document.getElementById('actionToast');
            const toastMessage = document.getElementById('toastMessage');
            if (!toastElement || !toastMessage || !window.bootstrap) {
                if (type === 'success') window.showSuccess?.(message);
                else window.showError?.(message);
                return;
            }
            toastMessage.textContent = message;
            toastElement.classList.remove('bg-success', 'bg-danger', 'bg-warning', 'bg-info');
            toastElement.classList.add(type === 'success' ? 'bg-success' : 'bg-danger');
            bootstrap.Toast.getOrCreateInstance(toastElement, { autohide: true, delay: 5000 }).show();
        },

        confirmRestartAfterChange() {
            const confirmation = this.t(
                'mapManagement.restartRequiredConfirm',
                'Map management configuration has changed. The server must be restarted for the changes to take effect.\n\nRestart the server now?'
            );
            if (confirm(confirmation)) {
                window.dispatchEvent(new CustomEvent('map-restart-server'));
            }
        },

        applyConfig(data) {
            this.status = { ...this.status, ...data };
            this.maps = Array.isArray(data.maps) ? data.maps : [];
            this.content = data.content || '';
            this.revision = data.revision || null;
            if (data.config_error) this.error = data.config_error;
        },

        applyPluginConfig(data) {
            const fields = Array.isArray(data.fields) ? data.fields : [];
            const values = {};
            fields.forEach((field) => {
                values[field.key] = field.value;
            });
            this.pluginConfig = {
                fields,
                values,
                revision: data.revision || null,
                file_exists: Boolean(data.plugin_config_file_exists),
                unsupported_fields: Array.isArray(data.unsupported_fields) ? data.unsupported_fields : []
            };
            this.status = { ...this.status, ...data };
            this.pluginConfigError = data.config_error || '';
        },

        applyCustomSync(data) {
            this.customSync = {
                ...this.customSync,
                ...data,
                interval_seconds: Math.max(300, Number(data.interval_seconds) || 3600)
            };
        },

        formatSyncTime(value) {
            if (!value) return this.t('mapManagement.syncNever', 'Never');
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
        },

        pluginConfigGroups() {
            const order = ['vote', 'rtv', 'extend', 'mapPool', 'mapChange', 'display', 'other'];
            return order.map((name) => ({
                name,
                fields: this.pluginConfig.fields.filter((field) => (field.group || 'other') === name)
            })).filter((group) => group.fields.length > 0);
        },

        pluginConfigGroupLabel(group) {
            return this.t(`mapManagement.pluginConfigGroups.${group}`, group);
        },

        pluginConfigFieldLabel(key) {
            return this.t(`mapManagement.pluginConfigFields.${key}.label`, key);
        },

        pluginConfigFieldDescription(key) {
            return this.t(`mapManagement.pluginConfigFields.${key}.description`, '');
        },

        pluginConfigFieldId(key) {
            return `mapchooser-config-${String(key).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
        },

        mapKey(map) {
            return `${map.workshop_id}:${map.name}`;
        },

        presetName(preset) {
            const fallbackNames = {
                official: 'All official maps',
                kz: 'KZ maps',
                ze: 'ZE maps'
            };
            return this.t(
                `mapManagement.presets.${preset}.name`,
                fallbackNames[preset] || preset.toUpperCase()
            );
        },

        filteredMaps() {
            const query = this.searchQuery.trim().toLocaleLowerCase();
            if (!query) return this.maps;
            return this.maps.filter((map) => [
                map.name,
                map.filename,
                map.updated_name,
                map.workshop_id
            ].filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
        },

        async load(force = false) {
            if (this.loading || (this.loaded && !force)) return;
            this.loading = true;
            this.error = '';
            try {
                const statusResponse = await authFetch(`/servers/${this.serverId}/maps/status`);
                const statusData = await statusResponse.json().catch(() => ({}));
                if (!statusResponse.ok) {
                    throw new Error(this.errorMessage(
                        statusData,
                        this.t('mapManagement.statusFailed', 'Failed to check map-management prerequisites')
                    ));
                }
                this.status = { ...this.status, ...statusData };
                this.loaded = true;

                if (!this.status.ready) {
                    this.maps = [];
                    this.content = '';
                    this.revision = null;
                    this.pluginConfig = {
                        fields: [],
                        values: {},
                        revision: null,
                        file_exists: false,
                        unsupported_fields: []
                    };
                    return;
                }

                const configResponse = await authFetch(`/servers/${this.serverId}/maps`);
                const configData = await configResponse.json().catch(() => ({}));
                if (!configResponse.ok) {
                    throw new Error(this.errorMessage(
                        configData,
                        this.t('mapManagement.loadFailed', 'Failed to load maps.txt')
                    ));
                }
                this.applyConfig(configData);
                await Promise.all([this.loadPluginConfig(), this.loadCustomSync()]);
            } catch (error) {
                this.error = error.message || String(error);
                this.loaded = true;
            } finally {
                this.loading = false;
            }
        },

        async loadPluginConfig() {
            if (this.pluginConfigLoading || !this.status.ready) return;
            this.pluginConfigLoading = true;
            this.pluginConfigError = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/plugin-config`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.pluginConfigLoadFailed', 'Failed to load MapChooser config.json')
                    ));
                }
                this.applyPluginConfig(data);
            } catch (error) {
                this.pluginConfigError = error.message || String(error);
            } finally {
                this.pluginConfigLoading = false;
            }
        },

        async loadCustomSync() {
            if (this.customSyncLoading || !this.status.ready) return;
            this.customSyncLoading = true;
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/custom-sync`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.customSyncLoadFailed', 'Failed to load sync settings')
                    ));
                }
                this.applyCustomSync(data);
            } catch (error) {
                this.error = error.message || String(error);
            } finally {
                this.customSyncLoading = false;
            }
        },

        async savePluginConfig() {
            if (this.pluginConfigSaving || !this.pluginConfig.fields.length) return;
            this.pluginConfigSaving = true;
            this.pluginConfigError = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/plugin-config`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        values: this.pluginConfig.values,
                        expected_revision: this.pluginConfig.revision
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.pluginConfigSaveFailed', 'Failed to save MapChooser config.json')
                    ));
                }
                this.applyPluginConfig(data);
                this.notify(
                    this.t('mapManagement.pluginConfigSaved', 'MapChooser settings saved successfully'),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.pluginConfigError = error.message || String(error);
                this.notify(this.pluginConfigError, 'error');
            } finally {
                this.pluginConfigSaving = false;
            }
        },

        async saveConfig() {
            this.saving = true;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        content: this.content,
                        expected_revision: this.revision
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.saveFailed', 'Failed to save maps.txt')
                    ));
                }
                this.applyConfig(data);
                this.notify(
                    this.t('mapManagement.saved', 'Map configuration saved successfully'),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.saving = false;
            }
        },

        async addMap() {
            if (!this.addForm.workshop_id || this.adding) return;
            this.adding = true;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.addForm)
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.addFailed', 'Failed to add map')
                    ));
                }
                this.applyConfig(data);
                const addedName = data.added_map?.name || this.addForm.name || this.addForm.workshop_id;
                this.notify(
                    this.t('mapManagement.added', 'Added {name} to the map pool', { name: addedName }),
                    'success'
                );
                this.confirmRestartAfterChange();
                this.addForm = {
                    workshop_id: '',
                    name: '',
                    enabled: true,
                    min_players: 0,
                    only_nominate: false,
                    restricted_times: ''
                };
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.adding = false;
            }
        },

        async applyPreset(preset) {
            if (this.presetApplying || !this.revision) return;
            const presetName = this.presetName(preset);
            const confirmation = this.t(
                'mapManagement.presetConfirm',
                'Replace the current map pool with {name}?',
                { name: presetName }
            );
            if (!confirm(confirmation)) return;

            this.presetApplying = preset;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/preset`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        preset,
                        expected_revision: this.revision,
                        plugin_config_expected_revision: this.pluginConfig.revision
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.presetFailed', 'Failed to switch map preset')
                    ));
                }
                this.applyConfig(data);
                if (data.plugin_config) this.applyPluginConfig(data.plugin_config);
                this.notify(
                    this.t(
                        'mapManagement.presetApplied',
                        'Switched to {name} ({count} maps)',
                        { name: presetName, count: data.map_count ?? this.maps.length }
                    ),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.presetApplying = '';
            }
        },

        async saveCustomSync(showNotification = true) {
            if (this.customSyncSaving || !this.customSync.url) return false;
            this.customSyncSaving = true;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/custom-sync`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: this.customSync.url,
                        enabled: Boolean(this.customSync.enabled),
                        interval_seconds: Math.max(300, Number(this.customSync.interval_seconds) || 300)
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.customSyncSaveFailed', 'Failed to save sync settings')
                    ));
                }
                this.applyCustomSync(data);
                if (showNotification) {
                    this.notify(
                        this.t('mapManagement.customSyncSaved', 'Sync settings saved'),
                        'success'
                    );
                }
                return true;
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
                return false;
            } finally {
                this.customSyncSaving = false;
            }
        },

        async runCustomSync() {
            if (this.customSyncRunning || this.customSyncSaving || !this.customSync.url) return;
            const saved = await this.saveCustomSync(false);
            if (!saved) return;

            this.customSyncRunning = true;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/custom-sync/run`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ expected_revision: this.revision })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.customSyncRunFailed', 'Failed to synchronize map pool')
                    ));
                }
                this.applyConfig(data);
                if (data.custom_sync) this.applyCustomSync(data.custom_sync);
                this.notify(
                    this.t(
                        'mapManagement.customSyncComplete',
                        'Synchronized {count} maps',
                        { count: data.map_count ?? this.maps.length }
                    ),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
                await this.loadCustomSync();
            } finally {
                this.customSyncRunning = false;
            }
        },

        async uninstallMapChooser() {
            if (this.uninstalling) return;
            const warning = this.t(
                'mapManagement.uninstallConfirm',
                'Delete the entire MapChooser plugin folder? You must reinstall the plugin before configuring it again.'
            );
            if (!confirm(warning)) return;

            this.uninstalling = true;
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps/plugin`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirmation: 'UNINSTALL MAPCHOOSER' })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.uninstallFailed', 'Failed to uninstall MapChooser')
                    ));
                }
                this.status = {
                    ...this.status,
                    mapchooser_installed: false,
                    ready: false
                };
                this.maps = [];
                this.content = '';
                this.revision = null;
                this.notify(
                    this.t('mapManagement.uninstallComplete', 'MapChooser was removed'),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.uninstalling = false;
            }
        },

        async toggleMap(map) {
            const key = this.mapKey(map);
            if (this.mapActionKey) return;
            this.mapActionKey = key;
            this.mapActionType = 'toggle';
            this.error = '';
            const enabled = !map.enabled;
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: map.name,
                        workshop_id: map.workshop_id,
                        expected_revision: this.revision,
                        enabled
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.actionFailed', 'Failed to update map')
                    ));
                }
                this.applyConfig(data);
                this.notify(
                    enabled
                        ? this.t('mapManagement.enabledSuccess', 'Enabled {name}', { name: map.name })
                        : this.t('mapManagement.disabledSuccess', 'Disabled {name}', { name: map.name }),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.mapActionKey = '';
                this.mapActionType = '';
            }
        },

        async deleteMap(map) {
            const confirmation = this.t(
                'mapManagement.deleteConfirm',
                'Remove {name} from the map pool? Downloaded Workshop files will not be deleted.',
                { name: map.name }
            );
            if (!confirm(confirmation) || this.mapActionKey) return;

            this.mapActionKey = this.mapKey(map);
            this.mapActionType = 'delete';
            this.error = '';
            try {
                const response = await authFetch(`/servers/${this.serverId}/maps`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: map.name,
                        workshop_id: map.workshop_id,
                        expected_revision: this.revision
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(this.errorMessage(
                        data,
                        this.t('mapManagement.actionFailed', 'Failed to update map')
                    ));
                }
                this.applyConfig(data);
                this.notify(
                    this.t('mapManagement.deletedSuccess', 'Removed {name} from the map pool', { name: map.name }),
                    'success'
                );
                this.confirmRestartAfterChange();
            } catch (error) {
                this.error = error.message || String(error);
                this.notify(this.error, 'error');
            } finally {
                this.mapActionKey = '';
                this.mapActionType = '';
            }
        }
    };
}
