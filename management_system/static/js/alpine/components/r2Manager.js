/**
 * Alpine.js Component: r2Manager
 * 
 * Manages R2 storage file operations including:
 * - File/folder selection and bulk actions
 * - File filtering and search
 * - File operations (upload, delete, rename, download)
 * - Folder operations (create, delete)
 * - Storage statistics management
 * 
 * @returns {Object} Alpine.js component
 */
function r2Manager() {
    return {
        currentFilter: '',
        searchQuery: '',
        searchPending: false,
        searchQueued: false,
        selectedFiles: [],
        newFolderName: '',
        renameFileKey: '',
        renameFileOldName: '',
        renameFileNewName: '',
        renameIsFolder: false,
        renamePollTimer: null,
        statsLoaded: false,
        statsPending: false,
        statsStatusUrl: '',
        statsPollTimer: null,
        statsDelay: 2000,
        statsDisposed: false,
        mutationPending: false,
        is_root: true, // Will be set by x-init in template
        routes: {},
        
        init() {
            this.routes = {
                download: this.$el.dataset.r2DownloadUrl,
                rename: this.$el.dataset.r2RenameUrl,
                delete: this.$el.dataset.r2DeleteUrl,
                deleteM3u8: this.$el.dataset.r2DeleteM3u8Url,
                deleteFolder: this.$el.dataset.r2DeleteFolderUrl,
                metadata: this.$el.dataset.r2MetadataUrl,
                createFolder: this.$el.dataset.r2CreateFolderUrl,
                stats: this.$el.dataset.r2StatsUrl,
            };
            const params = new URLSearchParams(window.location.search);
            this.currentFilter = params.get('filter') || 'media';
            this.searchQuery = params.get('search') || '';

            // Load stats on full page load only.
            if (!this.statsLoaded) {
                this.loadStorageStats();
            }
            this.$el.querySelector('[data-r2-operation-dismiss]')?.addEventListener('click', () => this.dismissStorageOperation());
            this.resumeStorageOperation();
            this.statsWakeHandler = () => {
                if (document.hidden) return;
                if (this.statsStatusUrl) this.pollStorageStats();
                else if (!this.statsLoaded) this.loadStorageStats();
            };
            document.addEventListener('visibilitychange', this.statsWakeHandler);
            window.addEventListener('focus', this.statsWakeHandler);
        },

        destroy() {
            this.statsDisposed = true;
            clearTimeout(this.statsPollTimer);
            clearTimeout(this.renamePollTimer);
            document.removeEventListener('visibilitychange', this.statsWakeHandler);
            window.removeEventListener('focus', this.statsWakeHandler);
        },
        
        /**
         * Toggle file selection for bulk operations
         */
        toggleFileSelection(fileKey) {
            const index = this.selectedFiles.indexOf(fileKey);
            if (index > -1) {
                this.selectedFiles.splice(index, 1);
            } else {
                this.selectedFiles.push(fileKey);
            }
        },
        
        /**
         * Apply filter - HTMX partial swap, no full page reload
         */
        applyFilter() {
            const url = new URL(window.location.href);
            url.searchParams.set('filter', this.currentFilter);
            htmx.ajax('GET', url.toString(), { target: '#file-list-container', push: url.pathname + url.search });
        },
        
        /**
         * Search files - HTMX partial swap, no full page reload.
         * A search can scan several provider pages server-side, so requests are
         * serialized: while one search runs, only the latest query is queued
         * and issued after it settles.
         */
        searchFiles() {
            if (this.searchPending) {
                this.searchQueued = true;
                return;
            }
            const url = new URL(window.location.href);
            if (this.searchQuery.trim()) {
                url.searchParams.set('search', this.searchQuery);
            } else {
                url.searchParams.delete('search');
            }
            if (!window.htmx) {
                window.location.href = url.toString();
                return;
            }
            this.searchPending = true;
            const request = htmx.ajax('GET', url.toString(), { target: '#file-list-container', push: url.pathname + url.search });
            Promise.resolve(request).catch(() => {}).finally(() => {
                this.searchPending = false;
                if (this.searchQueued) {
                    this.searchQueued = false;
                    this.searchFiles();
                }
            });
        },
        
        /**
         * Download file via presigned URL
         */
        downloadFile(fileKey) {
            const url = new URL(this.routes.download, window.location.origin);
            url.searchParams.set('file_key', fileKey);
            window.open(url.toString(), '_blank');
        },
        
        /**
         * Refresh the file list region without a full page reload.
         * The GET request carries the current ?folder=&filter=&search= params so
         * the refreshed list matches the current view; Django returns the
         * r2_browse partial for the file-list-container HX-Target. innerHTML is
         * used so the #file-list-container card is preserved (the partial's
         * .aw-card-body becomes its content), matching the existing browse and
         * admin_shell patterns.
         */
        refreshFileList() {
            if (window.htmx) {
                htmx.ajax('GET', window.location.href, { target: '#file-list-container', swap: 'innerHTML' });
            } else {
                window.location.reload();
            }
        },

        /**
         * Hide a modal and fire ``onHidden`` after the fade animation ends.
         * Prevents a ``location.reload()`` from interrupting the hide transition
         * and leaving a stale backdrop in the DOM.
         */
        _hideModal(el, onHidden) {
            const modal = bootstrap.Modal.getOrCreateInstance(el);
            modal.hide();
            if (onHidden) {
                el.addEventListener('hidden.bs.modal', onHidden, { once: true });
            }
        },

        /**
         * Show rename modal
         */
        showRenameModal(fileKey, oldName, isFolder = false) {
            this.renameFileKey = fileKey;
            this.renameFileOldName = oldName;
            this.renameFileNewName = oldName;
            this.renameIsFolder = isFolder;
            const errorEl = document.getElementById('rename-file-error');
            if (errorEl) { errorEl.textContent = ''; errorEl.hidden = true; }
            bootstrap.Modal.getOrCreateInstance(document.getElementById('renameFileModal')).show();
        },

        resumeStorageOperation() {
            let raw = '';
            try { raw = window.localStorage.getItem('r2-storage-operation') || ''; } catch (error) {}
            if (!raw) return;
            let saved = null;
            try { saved = JSON.parse(raw); } catch (error) { return; }
            if (saved && saved.statusUrl) this.pollStorageOperation(saved.statusUrl);
        },

        watchStorageOperation(job) {
            if (!job || !job.status_url) return;
            if (this.renamePollTimer) window.clearTimeout(this.renamePollTimer);
            const panel = this.$el.querySelector('[data-r2-operation-progress]');
            if (panel) delete panel.dataset.refreshed;
            try {
                window.localStorage.setItem('r2-storage-operation', JSON.stringify({ statusUrl: job.status_url }));
            } catch (error) {}
            this.pollStorageOperation(job.status_url);
        },

        pollStorageOperation(url) {
            const panel = this.$el.querySelector('[data-r2-operation-progress]');
            if (!panel) return;
            panel.hidden = false;
            fetch(url, { credentials: 'same-origin', cache: 'no-store' })
                .then(response => response.json().then(data => {
                    if (!response.ok) throw Object.assign(new Error(data.error || gettext('Could not load storage operation progress')), { status: response.status });
                    return data;
                }))
                .then(data => {
                    const labels = {
                        queued: gettext('Queued'),
                        scanning: gettext('Finding files'),
                        copying: gettext('Copying files'),
                        verifying: gettext('Verifying copies'),
                        deleting: gettext('Removing old files'),
                        updating_references: gettext('Updating lesson links'),
                        rolling_back: gettext('Cleaning up partial copies'),
                        complete: gettext('Complete'),
                        failed: gettext('Failed')
                    };
                    const result = data.result || {};
                    const status = String(data.status || '');
                    const terminal = status === 'completed' || status === 'partial' || status === 'failed';
                    const heading = panel.querySelector('[data-r2-operation-heading]');
                    const phase = panel.querySelector('[data-r2-operation-phase]');
                    const count = panel.querySelector('[data-r2-operation-count]');
                    const bar = panel.querySelector('[data-r2-operation-bar]');
                    const barWrap = panel.querySelector('[data-r2-operation-bar-wrap]');
                    const error = panel.querySelector('[data-r2-operation-error]');
                    const warning = panel.querySelector('[data-r2-operation-warning]');
                    const dismiss = panel.querySelector('[data-r2-operation-dismiss]');
                    const processed = Math.max(0, Number(data.processed) || 0);
                    const total = Math.max(0, Number(data.total) || 0);
                    const progress = total > 0
                        ? Math.max(0, Math.min(100, Math.round((processed / total) * 100)))
                        : (terminal ? 100 : 0);
                    if (heading) heading.textContent = status === 'completed'
                        ? gettext('Storage operation complete')
                        : status === 'partial' ? gettext('Storage operation completed with warnings')
                            : status === 'failed' ? gettext('Storage operation failed')
                                : status === 'queued' ? gettext('Storage operation queued')
                                    : gettext('Storage operation in progress');
                    if (phase) phase.textContent = labels[result.phase] || labels[status] || result.phase || status || '';
                    if (count) count.textContent = total > 0
                        ? interpolate(gettext('%(done)s of %(total)s files'), { done: processed, total }, true)
                        : (processed > 0 ? interpolate(gettext('%(done)s files'), { done: processed }, true) : '');
                    if (bar) bar.style.width = progress + '%';
                    if (barWrap) barWrap.setAttribute('aria-valuenow', String(progress));
                    if (error) {
                        error.textContent = data.error || (status === 'failed' ? gettext('The operation failed.') : '');
                        error.hidden = !error.textContent;
                    }
                    if (warning) {
                        warning.textContent = result.warning || '';
                        warning.hidden = !warning.textContent;
                    }
                    if (dismiss) dismiss.hidden = !terminal;
                    if (terminal) {
                        if (status !== 'failed' && panel.dataset.refreshed !== '1') {
                            panel.dataset.refreshed = '1';
                            this.refreshFileList();
                        }
                        return;
                    }
                    this.renamePollTimer = window.setTimeout(() => this.pollStorageOperation(url), 2000);
                })
                .catch(error => {
                    if (error.status === 401 || error.status === 403 || error.status === 404) {
                        this.dismissStorageOperation();
                        return;
                    }
                    const phase = panel.querySelector('[data-r2-operation-phase]');
                    if (phase) phase.textContent = gettext('Waiting for storage operation progress');
                    this.renamePollTimer = window.setTimeout(() => this.pollStorageOperation(url), 5000);
                });
        },

        dismissStorageOperation() {
            if (this.renamePollTimer) window.clearTimeout(this.renamePollTimer);
            this.renamePollTimer = null;
            try { window.localStorage.removeItem('r2-storage-operation'); } catch (error) {}
            var progressPanel = this.$el.querySelector('[data-r2-operation-progress]');
            if (progressPanel) progressPanel.hidden = true;
        },
        
        /**
         * Rename file (or folder) via API
         */
        renameFile() {
            // ponytail: no single-flight guard - the server queues renames
            // independently, so a second rename must not be blocked.
            if (!this.renameFileNewName.trim() || this.renameFileNewName === this.renameFileOldName) {
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = gettext('Please enter a different name'); errorEl.hidden = false; }
                return;
            }

            let oldKey, newKey;

            if (this.renameIsFolder) {
                // Key is already a slash path like "Academic Year - 2025-2026/"
                const parts = this.renameFileKey.replace(/\/$/, '').split('/');
                const parentParts = parts.slice(0, -1);
                const parentPath = parentParts.length ? parentParts.join('/') + '/' : '';
                oldKey = this.renameFileKey;
                newKey = parentPath + this.renameFileNewName + '/';
            } else {
                const pathParts = this.renameFileKey.split('/');
                const directory = pathParts.slice(0, -1).join('/');
                oldKey = this.renameFileKey;
                newKey = directory ? `${directory}/${this.renameFileNewName}` : this.renameFileNewName;
            }

            const body = JSON.stringify({ old_key: oldKey, new_key: newKey, is_folder: this.renameIsFolder });

            let csrf = '';
            try {
                csrf = this.getCSRFToken();
            } catch (error) {
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = gettext('Failed to rename'); errorEl.hidden = false; }
                return;
            }
            if (!csrf) {
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = gettext('Failed to rename'); errorEl.hidden = false; }
                return;
            }
            fetch(this.routes.rename, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf
                },
                body
            })
            .then(response => response.json().then(data => {
                if (!response.ok) throw new Error(data.error || gettext('Rename failed'));
                return data;
            }))
            .then(data => {
                if (data.job_id && data.status_url) {
                    this._hideModal(document.getElementById('renameFileModal'), () => this.watchStorageOperation(data));
                } else {
                    const errorEl = document.getElementById('rename-file-error');
                    if (errorEl) { errorEl.textContent = gettext('Rename request was not accepted'); errorEl.hidden = false; }
                }
            })
            .catch(error => {
                console.error('Error renaming:', error);
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = error.message || gettext('Failed to rename'); errorEl.hidden = false; }
            });
        },
        
        /**
         * Delete single file
         */
        deleteFile(fileKey, fileName, fileExt) {
            const isM3u8 = (typeof fileKey === 'string' && fileKey.toLowerCase().endsWith('.m3u8'))
                || (typeof fileExt === 'string' && fileExt.toLowerCase() === '.m3u8');
            const run = () => {
                const endpoint = isM3u8 ? this.routes.deleteM3u8 : this.routes.delete;
                return fetch(endpoint, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRFToken() },
                    body: JSON.stringify({ file_key: fileKey })
                })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        // Remove from DOM instead of reloading
                        const card = this.$el.querySelector(`[data-file-key="${CSS.escape(fileKey)}"]`);
                        card?.closest('.col')?.remove();
                        this.selectedFiles = this.selectedFiles.filter(k => k !== fileKey);
                        if (window.Alpine) Alpine.store('notifications').add(gettext('File deleted successfully'), 'success');
                    } else {
                        if (window.Alpine) Alpine.store('notifications').add(data.error || gettext('Delete failed'), 'danger');
                    }
                })
                .catch(() => {
                    if (window.Alpine) Alpine.store('notifications').add(gettext('Delete request failed'), 'danger');
                });
            };
            this.confirmMutation(interpolate(gettext('Are you sure you want to delete %(name)s?'), { name: fileName }, true), run);
        },
        
        /**
         * Delete folder recursively
         */
        deleteFolder(folderId) {
            const run = () => {
                return fetch(this.routes.deleteFolder, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRFToken() },
                    body: JSON.stringify({ folder_path: folderId, recursive: true })
                })
                .then(r => r.json())
                .then(data => {
                    if (data.job_id && data.status_url) {
                        this.watchStorageOperation(data);
                        if (window.Alpine) Alpine.store('notifications').add(data.message || gettext('Folder deletion was queued.'), 'info');
                        return;
                    }
                    if (data.success) {
                        const card = this.$el.querySelector(`[data-folder-id="${CSS.escape(folderId)}"]`);
                        card?.closest('.col')?.remove();
                        if (window.Alpine) Alpine.store('notifications').add(gettext('Folder deleted successfully'), 'success');
                    } else {
                        if (window.Alpine) Alpine.store('notifications').add(data.error || gettext('Delete failed'), 'danger');
                    }
                })
                .catch(() => {
                    if (window.Alpine) Alpine.store('notifications').add(gettext('Delete request failed'), 'danger');
                });
            };
            this.confirmMutation(gettext('Are you sure you want to delete this folder and all its contents?'), run);
        },
        
        /**
         * Delete selected files in bulk
         */
        deleteSelectedFiles() {
            if (this.selectedFiles.length === 0) return;
            const run = () => {
                const filesToDelete = [...this.selectedFiles];
                return Promise.all(filesToDelete.map(fileKey => {
                    // Manifests must clean their segment children before the
                    // parent, so route them to the smart m3u8 endpoint.
                    const endpoint = (typeof fileKey === 'string' && fileKey.toLowerCase().endsWith('.m3u8'))
                        ? this.routes.deleteM3u8
                        : this.routes.delete;
                    return fetch(endpoint, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRFToken() },
                        body: JSON.stringify({ file_key: fileKey })
                    }).then(r => r.ok ? { fileKey, success: true } : { fileKey, success: false });
                }))
                .then(results => {
                    const succeeded = results.filter(r => r.success).map(r => r.fileKey);
                    const failed = results.filter(r => !r.success).length;
                    // Remove successful items from DOM
                    succeeded.forEach(fileKey => {
                        const card = this.$el.querySelector(`[data-file-key="${CSS.escape(fileKey)}"]`);
                        card?.closest('.col')?.remove();
                    });
                    this.selectedFiles = this.selectedFiles.filter(k => !succeeded.includes(k));
                    if (window.Alpine) {
                        if (succeeded.length) Alpine.store('notifications').add(
                            interpolate(gettext('%(count)s file(s) deleted'), { count: succeeded.length }, true),
                            'success'
                        );
                        if (failed) Alpine.store('notifications').add(
                            interpolate(gettext('%(count)s file(s) failed to delete'), { count: failed }, true),
                            'danger'
                        );
                    }
                })
                .catch(() => {
                    if (window.Alpine) Alpine.store('notifications').add(gettext('Bulk delete failed'), 'danger');
                });
            };
            this.confirmMutation(interpolate(gettext('Are you sure you want to delete %(count)s selected file(s)?'), { count: this.selectedFiles.length }, true), run);
        },

        confirmMutation(message, callback) {
            const execute = () => {
                if (this.mutationPending) return;
                this.mutationPending = true;
                // Resolve first so a synchronous throw inside the mutation
                // still reaches `finally`: otherwise one failed action would
                // leave the guard set and silently block every later
                // delete/rename until a full page reload.
                return Promise.resolve().then(() => callback()).finally(() => { this.mutationPending = false; });
            };
            if (typeof window.appConfirm === 'function') window.appConfirm(message, execute);
            else execute();
        },
        
        /**
         * Get file metadata and show in modal
         */
        getFileMetadata(fileKey) {
            const infoModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('infoModal'));
            const bodyEl = document.getElementById('info-modal-body');
            
            bodyEl.replaceChildren();
            const spinnerWrap = document.createElement('div');
            spinnerWrap.className = 'd-flex justify-content-center';
            const spinner = document.createElement('div');
            spinner.className = 'spinner-border text-primary';
            spinner.setAttribute('role', 'status');
            const spinnerLabel = document.createElement('span');
            spinnerLabel.className = 'visually-hidden';
            spinnerLabel.textContent = gettext('Loading...');
            spinner.appendChild(spinnerLabel); spinnerWrap.appendChild(spinner); bodyEl.appendChild(spinnerWrap);
            
            infoModal.show();
            
            const metadataUrl = new URL(this.routes.metadata, window.location.origin);
            metadataUrl.searchParams.set('file_key', fileKey);
            fetch(metadataUrl.toString())
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        const metadata = data.metadata;
                        bodyEl.replaceChildren();
                        const table = document.createElement('table'); table.className = 'table table-sm';
                        [[gettext('Name'), metadata.name], [gettext('Size'), metadata.size_formatted], [gettext('Type'), metadata.content_type], [gettext('Last Modified'), metadata.last_modified], [gettext('Path'), fileKey]].forEach(([label, value]) => {
                            const row = document.createElement('tr'); const heading = document.createElement('th'); const cell = document.createElement('td');
                            heading.textContent = label; cell.textContent = value == null ? '' : String(value); row.append(heading, cell); table.appendChild(row);
                        });
                        bodyEl.appendChild(table);
                    } else {
                        bodyEl.replaceChildren(); const error = document.createElement('p'); error.className = 'text-danger'; error.textContent = gettext('Failed to load metadata'); bodyEl.appendChild(error);
                    }
                })
                .catch(error => {
                    console.error('Metadata error:', error);
                    bodyEl.replaceChildren(); const errorMessage = document.createElement('p'); errorMessage.className = 'text-danger'; errorMessage.textContent = gettext('Error loading metadata'); bodyEl.appendChild(errorMessage);
                });
        },
        
        /**
         * Show create folder modal
         */
        showCreateFolderModal() {
            this.newFolderName = '';
            bootstrap.Modal.getOrCreateInstance(document.getElementById('createFolderModal')).show();
        },
        
        /**
         * Create new folder
         */
        createFolder() {
            if (this.newFolderName.trim() === '') {
                const errorEl = document.getElementById('create-folder-error');
                errorEl.textContent = gettext('Please enter a folder name');
                errorEl.hidden = false;
                return;
            }
            
            const currentFolder = new URLSearchParams(window.location.search).get('folder') || '';
            const folderPath = currentFolder ? `${currentFolder}/${this.newFolderName}` : this.newFolderName;
            
            fetch(this.routes.createFolder, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify({ folder_path: folderPath })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    this._hideModal(document.getElementById('createFolderModal'), () => this.refreshFileList());
                } else {
                    const errorEl = document.getElementById('create-folder-error');
                    errorEl.textContent = data.error;
                    errorEl.hidden = false;
                }
            })
            .catch(error => {
                console.error('Create folder error:', error);
                const errorEl = document.getElementById('create-folder-error');
                errorEl.textContent = gettext('Failed to create folder');
                errorEl.hidden = false;
            });
        },
        
        /**
         * Load storage statistics
         */
        async loadStorageStats(refresh = false) {
            if (this.statsPending || this.statsDisposed || document.hidden) return;
            this.statsPending = true;
            let refreshNeeded = false;
            try {
                const response = await fetch(this.routes.stats, {
                    cache: 'no-store', method: refresh ? 'POST' : 'GET',
                    headers: refresh ? {'X-CSRFToken': this.getCSRFToken()} : {},
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || gettext('Unavailable'));
                if (refresh) {
                    this.statsStatusUrl = data.status_url || '';
                    this.statsDelay = 2000;
                    this.scheduleStatsPoll();
                } else if (data.success) {
                    this.updateStatsDisplay(data.stats || {});
                    this.statsLoaded = true;
                    const stamp = Date.parse(data.stats?.refreshed_at || '');
                    refreshNeeded = Number.isFinite(stamp) && Date.now() - stamp > 300000;
                } else {
                    this.showStatsUnavailable(data.unavailable_reason || '');
                    refreshNeeded = Boolean(data.refresh_required);
                }
            } catch (error) {
                this.showStatsUnavailable('');
            } finally {
                this.statsPending = false;
            }
            if (refreshNeeded) this.loadStorageStats(true);
        },

        scheduleStatsPoll() {
            clearTimeout(this.statsPollTimer);
            if (!this.statsDisposed && !document.hidden && this.statsStatusUrl) {
                this.statsPollTimer = setTimeout(() => this.pollStorageStats(), this.statsDelay);
            }
        },

        async pollStorageStats() {
            if (this.statsPending || this.statsDisposed || document.hidden || !this.statsStatusUrl) return;
            clearTimeout(this.statsPollTimer);
            this.statsPending = true;
            let complete = false;
            try {
                const response = await fetch(this.statsStatusUrl, {cache: 'no-store'});
                if (!response.ok) throw new Error(gettext('Unavailable'));
                const data = await response.json();
                if (['completed', 'partial', 'failed'].includes(data.status)) {
                    this.statsStatusUrl = '';
                    complete = data.status === 'completed';
                    if (complete) this.statsLoaded = false;
                    if (!complete) this.showStatsUnavailable(data.error || '');
                }
                this.statsDelay = 2000;
            } catch (error) {
                this.statsDelay = Math.min(this.statsDelay * 2, 30000);
            } finally {
                this.statsPending = false;
            }
            if (complete) this.loadStorageStats();
            else this.scheduleStatsPoll();
        },

        showStatsUnavailable(reason) {
            // Never present an unavailable result as exact zero usage.
            this.statsLoaded = false;
            document.querySelectorAll('[id$="-approx"]').forEach(el => { el.style.display = 'none'; });
            ['stat-total-files', 'stat-total-storage', 'stat-video-files', 'stat-pdf-files'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.textContent = gettext('Unavailable');
            });
            const status = document.getElementById('stat-unavailable');
            if (status) {
                status.textContent = reason ? `${gettext('Unavailable')} (${reason})` : gettext('Unavailable');
                status.style.display = 'block';
            }
        },
        
        /**
         * Refresh storage statistics
         */
        refreshStats() {
            this.statsLoaded = false;
            this.loadStorageStats(true);
        },
        
        /**
         * Update statistics display
         */
        updateStatsDisplay(stats) {
            const status = document.getElementById('stat-unavailable');
            if (status) status.style.display = 'none';
            const refreshed = document.getElementById('stat-refreshed');
            if (refreshed && stats.refreshed_at) refreshed.textContent = gettext('Last updated') + ': ' + new Date(stats.refreshed_at).toLocaleString(document.documentElement.lang || 'ar');
            const totalFilesEl = document.getElementById('stat-total-files');
            const totalStorageEl = document.getElementById('stat-total-storage');
            const videoFilesEl = document.getElementById('stat-video-files');
            const pdfFilesEl = document.getElementById('stat-pdf-files');
            
            if (totalFilesEl) totalFilesEl.textContent = stats.total_files || 0;
            if (totalStorageEl) totalStorageEl.textContent = stats.total_size_formatted || '0 B';
            if (videoFilesEl) videoFilesEl.textContent = stats.by_kind?.video?.count || 0;
            if (pdfFilesEl) pdfFilesEl.textContent = stats.by_kind?.document?.count || stats.by_extension?.pdf?.count || 0;
            
            // Toggle approximate badges
            const show = stats.approximate ? 'block' : 'none';
            const approxEls = document.querySelectorAll('[id$="-approx"]');
            approxEls.forEach(el => el.style.display = show);
        },
        
        /**
         * Get CSRF token from body attribute
         */
        getCSRFToken() {
            const body = document.querySelector("body");
            // HTMX 4 renames inherited body attributes to hx-headers:inherited.
            const raw = body.getAttribute("hx-headers:inherited") || body.getAttribute("hx-headers");
            const headers = JSON.parse(raw || "{}");
            return headers['X-CSRFToken'];
        }
    };
}

// Export as global for template usage
window.r2Manager = r2Manager;
