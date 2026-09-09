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
        selectedFiles: [],
        newFolderName: '',
        renameFileKey: '',
        renameFileOldName: '',
        renameFileNewName: '',
        renameIsFolder: false,
        statsLoaded: false,
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
         * Apply filter — HTMX partial swap, no full page reload
         */
        applyFilter() {
            const url = new URL(window.location.href);
            url.searchParams.set('filter', this.currentFilter);
            htmx.ajax('GET', url.toString(), { target: '#file-list-container', push: url.pathname + url.search });
        },
        
        /**
         * Search files — HTMX partial swap, no full page reload
         */
        searchFiles() {
            const url = new URL(window.location.href);
            if (this.searchQuery.trim()) {
                url.searchParams.set('search', this.searchQuery);
            } else {
                url.searchParams.delete('search');
            }
            htmx.ajax('GET', url.toString(), { target: '#file-list-container', push: url.pathname + url.search });
        },
        
        /**
         * Download file via presigned URL
         */
        downloadFile(fileKey, fileName) {
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
         * .ad-card-body becomes its content), matching the existing browse and
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
            if (errorEl) { errorEl.textContent = ''; errorEl.classList.add('d-none'); }
            bootstrap.Modal.getOrCreateInstance(document.getElementById('renameFileModal')).show();
        },
        
        /**
         * Rename file (or folder) via API
         */
        renameFile() {
            if (!this.renameFileNewName.trim() || this.renameFileNewName === this.renameFileOldName) {
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = gettext('Please enter a different name'); errorEl.classList.remove('d-none'); }
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

            fetch(this.routes.rename, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    this._hideModal(document.getElementById('renameFileModal'), () => this.refreshFileList());
                } else {
                    const errorEl = document.getElementById('rename-file-error');
                    if (errorEl) { errorEl.textContent = data.error || gettext('Rename failed'); errorEl.classList.remove('d-none'); }
                }
            })
            .catch(error => {
                console.error('Error renaming:', error);
                const errorEl = document.getElementById('rename-file-error');
                if (errorEl) { errorEl.textContent = gettext('Failed to rename'); errorEl.classList.remove('d-none'); }
            });
        },
        
        /**
         * Delete single file
         */
        deleteFile(fileKey, fileName, fileExt) {
            const isM3u8 = fileExt === '.m3u8';
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
                return Promise.all(filesToDelete.map(fileKey =>
                    fetch(this.routes.delete, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRFToken() },
                        body: JSON.stringify({ file_key: fileKey })
                    }).then(r => r.ok ? { fileKey, success: true } : { fileKey, success: false })
                ))
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
                return Promise.resolve(callback()).finally(() => { this.mutationPending = false; });
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
                errorEl.classList.remove('d-none');
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
                    errorEl.classList.remove('d-none');
                }
            })
            .catch(error => {
                console.error('Create folder error:', error);
                const errorEl = document.getElementById('create-folder-error');
                errorEl.textContent = gettext('Failed to create folder');
                errorEl.classList.remove('d-none');
            });
        },
        
        /**
         * Load storage statistics
         */
        loadStorageStats() {
            fetch(this.routes.stats)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        this.updateStatsDisplay(data.stats);
                        this.statsLoaded = true;
                    }
                })
                .catch(error => {
                    console.error('Stats loading error:', error);
                });
        },
        
        /**
         * Refresh storage statistics
         */
        refreshStats() {
            this.statsLoaded = false;
            this.loadStorageStats();
        },
        
        /**
         * Update statistics display
         */
        updateStatsDisplay(stats) {
            const totalFilesEl = document.getElementById('stat-total-files');
            const totalStorageEl = document.getElementById('stat-total-storage');
            const videoFilesEl = document.getElementById('stat-video-files');
            const pdfFilesEl = document.getElementById('stat-pdf-files');
            
            if (totalFilesEl) totalFilesEl.textContent = stats.total_files || 0;
            if (totalStorageEl) totalStorageEl.textContent = stats.total_size_formatted || '0 B';
            if (videoFilesEl) videoFilesEl.textContent = stats.by_extension?.mp4?.count || 0;
            if (pdfFilesEl) pdfFilesEl.textContent = stats.by_extension?.pdf?.count || 0;
            
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
