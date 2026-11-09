// Browser voice recording for the Telegram support composer.
// The recording never auto-sends: the admin previews it, then attaches it to
// the existing multipart reply form and sends explicitly.
document.addEventListener('alpine:init', () => {
    Alpine.data('supportVoiceRecorder', () => ({
        recording: false,
        seconds: 0,
        blob: null,
        blobUrl: '',
        error: '',
        supported: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder),
        _stream: null,
        _recorder: null,
        _chunks: [],
        _timer: null,
        _maxTimer: null,

        init() {
            if (!this.supported) {
                this.error = this.$el.dataset.unsupported || 'Voice recording is not available in this browser.';
            }
        },

        destroy() {
            this._cleanup();
            this._revoke();
        },

        _cleanup() {
            if (this._timer) { clearInterval(this._timer); this._timer = null; }
            if (this._maxTimer) { clearTimeout(this._maxTimer); this._maxTimer = null; }
            if (this._stream) {
                this._stream.getTracks().forEach((track) => track.stop());
                this._stream = null;
            }
            this._recorder = null;
            this.recording = false;
        },

        _revoke() {
            if (this.blobUrl) { URL.revokeObjectURL(this.blobUrl); this.blobUrl = ''; }
        },

        _pickMime() {
            var candidates = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
            for (var i = 0; i < candidates.length; i += 1) {
                if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(candidates[i])) {
                    return candidates[i];
                }
            }
            return '';
        },

        async start() {
            if (!this.supported || this.recording) return;
            this.error = '';
            this._revoke();
            this.blob = null;
            try {
                this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                var mime = this._pickMime();
                this._recorder = mime ? new MediaRecorder(this._stream, { mimeType: mime }) : new MediaRecorder(this._stream);
                this._chunks = [];
                this._mime = mime || 'audio/webm';
                this._recorder.addEventListener('dataavailable', (event) => {
                    if (event.data && event.data.size) this._chunks.push(event.data);
                });
                this._recorder.addEventListener('stop', () => {
                    this._finish();
                });
                this._recorder.start();
                this.recording = true;
                this.seconds = 0;
                this._timer = setInterval(() => {
                    this.seconds += 1;
                    if (this.seconds >= 300) this.stop();
                }, 1000);
                this._maxTimer = setTimeout(() => this.stop(), 300000);
            } catch (error) {
                this.error = this.$el.dataset.error || 'Microphone access was denied or unavailable.';
                this._cleanup();
            }
        },

        stop() {
            if (!this.recording || !this._recorder) return;
            try { this._recorder.stop(); } catch (error) { /* already stopped */ }
            this._cleanup();
        },

        _finish() {
            if (!this._chunks.length) {
                this.error = this.$el.dataset.errorEmpty || 'The recording was empty. Try again.';
                return;
            }
            var mime = this._mime || 'audio/webm';
            this.blob = new Blob(this._chunks, { type: mime });
            this.blobUrl = URL.createObjectURL(this.blob);
        },

        discard() {
            this._cleanup();
            this._revoke();
            this.blob = null;
            this.error = '';
        },

        attach() {
            if (!this.blob) return;
            var form = this.$el.closest('form');
            var input = form && form.querySelector('input[type="file"]');
            if (!input) {
                this.error = this.$el.dataset.errorAttach || 'The attachment field is unavailable.';
                return;
            }
            var mime = this.blob.type || 'audio/webm';
            var extension = mime.indexOf('ogg') !== -1 ? 'ogg' : (mime.indexOf('mp4') !== -1 ? 'm4a' : 'webm');
            var file = new File([this.blob], 'voice-recording.' + extension, { type: mime });
            var transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        },
    }));
});
