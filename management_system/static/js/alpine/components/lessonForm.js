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
        // Translations
        init() {
            // Initialize on page load and after HTMX swaps
            this.initializeDriveFiles();
            
            // Listen for HTMX afterSwap events
            this.$el.addEventListener('htmx:afterSwap', () => {
                this.initializeDriveFiles();
            });
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

            // Auto-detect file type from name
            let fileType = 'book';
            const nameLower = name.toLowerCase();
            if (nameLower.includes('audio')) fileType = 'audio';
            if (nameLower.includes('video')) fileType = 'video';

            // Create video card HTML
            const videoCard = `
                <div class="card video-container mb-3">
                    <input type="hidden" name="videos" value="${id}">
                    <input type="hidden" name="videos_name" value="${name}">
                    <div class="card-body p-3">
                        <button type="button" class="btn-close position-absolute top-0 end-0 m-2" 
                                @click="removeFile($event)" aria-label="${this.getTranslation('close')}"></button>
                        <p class="fw-bold mb-2 text-primary">${name}</p>
                        <select name="files_type" class="form-select form-select-sm">
                            <option value="video" ${fileType === 'video' ? 'selected' : ''}>${this.getTranslation('video')}</option>
                            <option value="audio" ${fileType === 'audio' ? 'selected' : ''}>${this.getTranslation('audioRecording')}</option>
                            <option value="book" ${fileType === 'book' ? 'selected' : ''}>${this.getTranslation('book')}</option>
                        </select>
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
            container.appendChild(tempDiv.firstElementChild);
        },

        /**
         * Remove file from selected lectures
         */
        removeFile(event) {
            event.currentTarget.closest('.video-container').remove();
        },

        /**
         * Move file up in order
         */
        moveUp(event) {
            const container = event.currentTarget.closest('.video-container');
            const prev = container.previousElementSibling;
            
            if (prev) {
                container.parentNode.insertBefore(container, prev);
            }
        },

        /**
         * Move file down in order
         */
        moveDown(event) {
            const container = event.currentTarget.closest('.video-container');
            const next = container.nextElementSibling;
            
            if (next) {
                container.parentNode.insertBefore(next, container);
            }
        }
    };
}

// Export as global for template usage
window.lessonForm = lessonForm;
