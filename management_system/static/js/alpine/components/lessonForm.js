/**
 * Alpine.js Component: lessonForm
 * 
 * Manages lesson form functionality including:
 * - File selection from Google Drive
 * - Lecture ordering (move up/down)
 * - File type selection (video/audio/book)
 * - Auto-detection of file types from names
 * 
 * @returns {Object} Alpine.js component
 */
function lessonForm() {
    return {
        selectedLectureCount: 0,

        // Translations
        init() {
            // Initialize on page load and after HTMX swaps
            this.initializeDriveFiles();
            this.updateSelectedLectureCount();
            this.reindexParts();

            // Listen for HTMX afterSwap events
            this.$el.addEventListener('htmx:after:swap', () => {
                this.initializeDriveFiles();
            });
        },

        escapeHtml(value) {
            return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        },

        /**
         * Get translated string
         */
        getTranslation(key) {
            const root = this.$root || document.getElementById('content');
            const translations = {
                close: root?.dataset.lessonClose,
                video: root?.dataset.lessonVideo,
                audioRecording: root?.dataset.lessonAudioRecording,
                book: root?.dataset.lessonBook,
                moveUp: root?.dataset.lessonMoveUp,
                moveDown: root?.dataset.lessonMoveDown,
                part: root?.dataset.lessonPart,
                download: root?.dataset.lessonDownload,
            };
            return translations[key] || key;
        },

        /**
         * Initialize drive files display
         */
        initializeDriveFiles() {
            const files = this.$el.querySelectorAll('#drive-files .file-icon');
            
            // Hide .ts segment files
            files.forEach(file => {
                if (file.dataset.id && file.dataset.id.endsWith('.ts')) {
                    file.closest('.col')?.remove();
                }
            });

            // Clean up .m3u8 extensions from display
            this.$el.querySelectorAll('#drive-files .card-title').forEach(title => {
                if (title.textContent.trim().endsWith('.m3u8')) {
                    title.textContent = title.textContent.trim().replace('.m3u8', '');
                }
            });
        },

        updateSelectedLectureCount() {
            const container = this.$el.querySelector('#videos-input');
            this.selectedLectureCount = container
                ? container.querySelectorAll('.video-container').length
                : 0;
        },

        /**
         * Add file to selected lectures
         */
        addFile(event) {
            const fileIcon = event.currentTarget;
            if (!fileIcon || !fileIcon.dataset) return;
            const id = fileIcon.dataset.id;
            if (!id) return;

            const cardBody = fileIcon.closest('.card-body');
            const titleEl = cardBody && cardBody.querySelector('.card-title');
            const name = titleEl ? titleEl.textContent.trim() : id;
            if (!name) return;

            // Auto-detect file type from name: audio always wins; a name
            // containing "lecture" (without "audio") defaults to video;
            // anything else stays a book.
            const nameLower = name.toLowerCase();
            let fileType = 'book';
            if (nameLower.includes('video') || nameLower.includes('lecture')) fileType = 'video';
            if (nameLower.includes('audio')) fileType = 'audio';

            this.appendLessonFile(id, name, fileType);
            this.hasChanges = true;
        },

        captureDraftRows() {
            return {kind: 'lesson', rows: Array.from(this.$el.querySelectorAll('.video-container'), card => ({
                id: card.querySelector('[name="videos"]').value,
                name: card.querySelector('[name="videos_name"]').value,
                type: card.querySelector('[name="files_type"]').value,
            }))};
        },

        restoreDraftRows(data) {
            if (data.kind !== 'lesson' || !Array.isArray(data.rows) || data.rows.length > 500
                || data.rows.some(row => typeof row.id !== 'string' || typeof row.name !== 'string'
                    || !['video', 'audio', 'book'].includes(row.type))) throw new Error('Invalid lesson draft');
            this.$el.querySelectorAll('.video-container').forEach(card => card.remove());
            data.rows.forEach(row => this.appendLessonFile(row.id, row.name, row.type));
        },

        afterDraftRestore() {
            this.updateSelectedLectureCount();
            this.reindexParts();
            this.hasChanges = false;
        },

        appendLessonFile(id, name, fileType) {

            // Escape the key/name before interpolating them into markup: an R2
            // key is admin-supplied but must not be able to break out of an
            // attribute or inject a node.
            const safeId = this.escapeHtml(id);
            const safeName = this.escapeHtml(name);

            // Create video card HTML
            const videoCard = `
                <div class="card video-container mb-3">
                    <input type="hidden" name="videos" value="${safeId}">
                    <input type="hidden" name="videos_name" value="${safeName}">
                    <div class="card-body p-3">
                        <button type="button" class="btn-close position-absolute top-0 end-0 m-2" 
                                @click="removeFile($event)" aria-label="${this.getTranslation('close')}"></button>
                        <p class="fw-bold mb-2 text-primary d-flex justify-content-between align-items-start gap-2 pe-5">
                            <span class="text-break">${safeName}</span>
                            <span class="badge text-bg-secondary part-badge flex-shrink-0" hidden></span>
                        </p>
                        <select name="files_type" class="form-select form-select-sm" @change="toggleDownloadInput($event)">
                            <option value="video" ${fileType === 'video' ? 'selected' : ''}>${this.getTranslation('video')}</option>
                            <option value="audio" ${fileType === 'audio' ? 'selected' : ''}>${this.getTranslation('audioRecording')}</option>
                            <option value="book" ${fileType === 'book' ? 'selected' : ''}>${this.getTranslation('book')}</option>
                        </select>
                        <div class="download-key-field mt-2${fileType === 'audio' ? '' : ' d-none'}">
                            <label class="form-label small mb-1">${this.getTranslation('download')}</label>
                            <input type="text" name="download_ids" class="form-control form-control-sm download-key-input"
                                   value="" placeholder="Downloadable Files/name.mp3"
                                   aria-label="${this.getTranslation('download')}">
                        </div>
                        <div class="d-flex align-items-center gap-2 mt-2">
                            <label class="form-label small mb-0">${this.getTranslation('part')}</label>
                            <input type="number" min="1" name="part_groups" class="form-control form-control-sm part-group-input"
                                   style="max-width:6rem" value="" @input="reindexParts()"
                                   aria-label="${this.getTranslation('part')}">
                        </div>
                    </div>
                    <div class="card-footer bg-light p-1 text-end">
                        <button type="button" class="btn btn-sm btn-outline-secondary border-0" 
                                @click="moveUp($event)" title="${this.getTranslation('moveUp')}">
                            <i class="fas fa-arrow-up"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary border-0" 
                                @click="moveDown($event)" title="${this.getTranslation('moveDown')}">
                            <i class="fas fa-arrow-down"></i>
                        </button>
                    </div>
                </div>
            `;

            // Append to the page-level selected-lectures container
            const container = document.getElementById('videos-input');
            if (!container) return;

            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = videoCard;
            const card = tempDiv.firstElementChild;
            container.appendChild(card);
            this.assignDefaultGroup(card);
            this.updateSelectedLectureCount();
            this.reindexParts();
        },

        /**
         * Remove file from selected lectures
         */
        removeFile(event) {
            event.currentTarget.closest('.video-container').remove();
            this.updateSelectedLectureCount();
            this.reindexParts();
            this.hasChanges = true;
        },

        /**
         * Move file up in order
         */
        moveUp(event) {
            this.hasChanges = true;
            const container = event.currentTarget.closest('.video-container');
            const prev = container.previousElementSibling;
            
            if (prev) {
                container.parentNode.insertBefore(container, prev);
                this.reindexParts();
            }
        },

        /**
         * Move file down in order
         */
        moveDown(event) {
            this.hasChanges = true;
            const container = event.currentTarget.closest('.video-container');
            const next = container.nextElementSibling;
            
            if (next) {
                container.parentNode.insertBefore(next, container);
                this.reindexParts();
            }
        },

        /**
         * Grouping base for a media key: lowercase, extension dropped and a
         * trailing `_audio` stripped, so a video manifest and its `_audio`
         * sibling resolve to one part. Mirrors the server's _lesson_part_base.
         */
        partBase(id) {
            const raw = String(id || '');
            const slash = raw.lastIndexOf('/');
            let name = (slash >= 0 ? raw.slice(slash + 1) : raw).toLowerCase();
            const dot = name.lastIndexOf('.');
            if (dot > 0) name = name.slice(0, dot);
            return name.endsWith('_audio') ? name.slice(0, -6) : name;
        },

        /**
         * Default the part number for a newly added card: reuse the number of
         * an existing card with the same file base (so a video and its `_audio`
         * sibling join one part), otherwise take the next free number.
         */
        assignDefaultGroup(card) {
            const input = card.querySelector('.part-group-input');
            if (!input || input.value) return;
            const container = document.getElementById('videos-input');
            if (!container) return;
            const idInput = card.querySelector('input[name="videos"]');
            const base = this.partBase(idInput ? idInput.value : '');
            let siblingNumber = 0;
            let max = 0;
            container.querySelectorAll('.video-container').forEach(other => {
                const otherInput = other.querySelector('.part-group-input');
                const value = otherInput ? (parseInt(otherInput.value, 10) || 0) : 0;
                if (value > max) max = value;
                if (other !== card && base) {
                    const otherId = other.querySelector('input[name="videos"]');
                    if (value && this.partBase(otherId ? otherId.value : '') === base) {
                        siblingNumber = value;
                    }
                }
            });
            input.value = String(siblingNumber || max + 1);
        },

        /**
         * Show a shared "Part N" badge on every selected card and colour the
         * card's leading edge with the same colour. The number comes from the
         * editable `part_groups` input, so the operator changes the relation by
         * giving two files the same number (or a new one to split them).
         */
        reindexParts() {
            const container = document.getElementById('videos-input');
            if (!container) return;
            const palette = ['#2f5d73', '#b4853a', '#5a7d4c', '#7a4c7a', '#b45a3a'];
            container.querySelectorAll('.video-container').forEach(card => {
                const input = card.querySelector('.part-group-input');
                const badge = card.querySelector('.part-badge');
                if (!badge) return;
                const number = input ? (parseInt(input.value, 10) || 0) : 0;
                if (number > 0) {
                    const color = palette[(number - 1) % palette.length];
                    card.style.borderInlineStartWidth = '4px';
                    card.style.borderInlineStartStyle = 'solid';
                    card.style.borderInlineStartColor = color;
                    badge.hidden = false;
                    badge.textContent = this.getTranslation('part') + ' ' + number;
                    badge.style.backgroundColor = color;
                    badge.style.color = '#fff';
                } else {
                    card.style.removeProperty('border-inline-start-width');
                    card.style.removeProperty('border-inline-start-style');
                    card.style.removeProperty('border-inline-start-color');
                    badge.hidden = true;
                    badge.textContent = '';
                    badge.style.removeProperty('background-color');
                    badge.style.removeProperty('color');
                }
            });
        },

        /**
         * Only an audio link has a downloadable MP3, so the download-key field
         * is shown for audio and hidden (and emptied) for video/book.
         */
        toggleDownloadInput(event) {
            const card = event.currentTarget.closest('.video-container');
            if (!card) return;
            const field = card.querySelector('.download-key-field');
            if (!field) return;
            const isAudio = event.currentTarget.value === 'audio';
            field.classList.toggle('d-none', !isAudio);
            if (!isAudio) {
                const input = field.querySelector('.download-key-input');
                if (input) input.value = '';
            }
        }
    };
}

// Export as global for template usage
window.lessonForm = lessonForm;
