function mapManagement(serverId) {
    return {
        serverId,
        loaded: false,
        loading: false,
        saving: false,
        adding: false,
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
            plugin_config_path: ''
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
                await this.loadPluginConfig();
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
