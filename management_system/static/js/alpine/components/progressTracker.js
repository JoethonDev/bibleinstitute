const PROGRESS_HEARTBEAT_INTERVAL_MS = 60000;
const PROGRESS_QUEUE_PREFIX = 'lms:progress:v1';
const PROGRESS_QUEUE_MAX_ENTRIES = 100;
const PROGRESS_QUEUE_MAX_BYTES = 1024 * 1024;
const PROGRESS_QUEUE_MAX_RANGES = 200;
const MAX_MEDIA_RECOVERY_RETRIES = 5;
const MEDIA_RECOVERY_BASE_DELAY_MS = 2000;
const MEDIA_RECOVERY_MAX_DELAY_MS = 15000;
const MEDIA_GATE_REFRESH_EARLY_MS = 60000;
const MEDIA_GATE_REFRESH_JITTER_MS = 180000;
const MEDIA_GATE_REFRESH_LATE_MS = 120000;

function progressQueueKey(studentId, lessonId, partId, sessionId) {
    return [PROGRESS_QUEUE_PREFIX, studentId, lessonId, partId, sessionId]
        .map((value, index) => index === 0 ? value : encodeURIComponent(String(value)))
        .join(':');
}

function progressQueueEntryMatchesKey(entry, key, studentId) {
    return Boolean(
        entry?.version === 1
        && entry.key === key
        && String(entry.studentId) === String(studentId)
        && entry.lessonId != null
        && entry.partId != null
        && entry.sessionId != null
        && progressQueueKey(entry.studentId, entry.lessonId, entry.partId, entry.sessionId) === key
    );
}

function progressQueueNormalizeRanges(ranges) {
    if (!Array.isArray(ranges)) return [];
    const normalized = ranges.filter(range => (
        Array.isArray(range)
        && range.length === 2
        && typeof range[0] === 'number'
        && typeof range[1] === 'number'
        && Number.isFinite(range[0])
        && Number.isFinite(range[1])
        && Number(range[0]) >= 0
        && Number(range[1]) > Number(range[0])
    )).map(range => {
        const start = Math.floor(Number(range[0]) * 100 + 0.5 + 1e-9) / 100;
        const end = Math.floor(Number(range[1]) * 100 + 0.5 + 1e-9) / 100;
        return [start, end];
    }).filter(([start, end]) => end > start).sort((left, right) => left[0] - right[0]);
    const merged = [];
    normalized.forEach(([start, end]) => {
        const last = merged[merged.length - 1];
        if (last && start <= last[1]) last[1] = Math.max(last[1], end);
        else merged.push([start, end]);
    });
    return merged;
}

function progressQueueSubtractRanges(ranges, coveredRanges) {
    const requested = progressQueueNormalizeRanges(ranges);
    const covered = progressQueueNormalizeRanges(coveredRanges);
    const remaining = [];
    let coveredIndex = 0;
    requested.forEach(([start, end]) => {
        let cursor = start;
        while (coveredIndex < covered.length && covered[coveredIndex][1] <= cursor) {
            coveredIndex += 1;
        }
        let index = coveredIndex;
        while (index < covered.length && covered[index][0] < end) {
            const [coveredStart, coveredEnd] = covered[index];
            if (coveredStart > cursor) remaining.push([cursor, Math.min(coveredStart, end)]);
            cursor = Math.max(cursor, coveredEnd);
            if (cursor >= end) break;
            index += 1;
        }
        if (cursor < end) remaining.push([cursor, end]);
    });
    return progressQueueNormalizeRanges(remaining);
}

function progressQueueReconcilePending(pending, acknowledgement) {
    const payload = pending?.payload;
    if (
        !payload
        || !acknowledgement
        || acknowledgement.session_id !== payload.session_id
        || Number(acknowledgement.sequence) !== Number(payload.sequence)
        || !['ok', 'resync'].includes(acknowledgement.status)
    ) return null;
    const confirmed = acknowledgement.status === 'ok'
        ? [
            ...(acknowledgement.accepted_ranges || []),
            ...(acknowledgement.already_saved_ranges || []),
        ]
        : (acknowledgement.already_saved_ranges || []);
    const requestedActiveSeconds = Math.max(0, Number(payload.active_seconds) || 0);
    const lastSequence = Math.max(0, Number(acknowledgement.last_sequence) || 0);
    const retryGap = acknowledgement.status === 'resync'
        && acknowledgement.reason === 'sequence_gap'
        && lastSequence < Number(payload.sequence);
    const creditedActiveSeconds = acknowledgement.status === 'ok'
        ? Math.min(requestedActiveSeconds, Math.max(0, Number(acknowledgement.credited_active_seconds) || 0))
        : 0;
    const remainingActiveSeconds = retryGap ? requestedActiveSeconds : 0;
    return {
        remainingRanges: progressQueueSubtractRanges(payload.ranges, confirmed),
        remainingActiveSeconds,
        unreconciledActiveSeconds: (
            (acknowledgement.status === 'ok' || (acknowledgement.status === 'resync' && !retryGap))
                ? Math.max(0, requestedActiveSeconds - creditedActiveSeconds)
                : 0
        ),
        nextSequence: acknowledgement.status === 'resync'
            ? lastSequence + 1
            : Number(payload.sequence) + 1,
        ended: Boolean(payload.ended),
    };
}

function progressTracker() {
    return {
        playedRanges: [],
        activeSecondsBuffer: 0,
        heartbeatSequence: 0,
        playbackSample: null,
        progressPercent: 0,
        sessionId: null,
        offeringId: null,
        lessonId: null,
        partId: null,
        studentId: null,
        fileIndex: null,
        startSessionUrl: null,
        heartbeatUrl: null,
        mediaUrl: null,
        gateRefreshUrl: null,
        gateToken: null,
        gateRefreshAt: null,
        gateValidUntil: null,
        interval: null,
        mediaRefreshTimer: null,
        mediaRefreshInFlight: false,
        mediaRefreshFailures: 0,
        mediaRecoveryPending: false,
        mediaRecoverySessionId: null,
        mediaAuthErrorHandler: null,
        onlineHandler: null,
        destroyed: false,
        segmentTokenLifetimeSeconds: 600,
        finalHeartbeatSent: false,
        pageExitHandler: null,
        progressUpdateHandler: null,
        visibilityHandler: null,
        positionHandler: null,
        accountStorageHandler: null,
        queueMemory: null,
        queueDispatching: null,
        queueStatusMessage: '',
        queueMessages: null,
        accountSuspended: false,
        queueLastPersistAt: 0,
        resumeSeconds: 0,
        resumeApplied: false,
        lastPositionBroadcastAt: 0,
        suppressPositionBroadcastUntil: 0,

        init() {
            this.offeringId = this.$el.dataset.offeringId;
            this.lessonId = this.$el.dataset.lessonId;
            this.partId = this.$el.dataset.partId;
            this.studentId = this.$el.dataset.studentId || '';
            this.fileIndex = this.$el.dataset.fileIndex;
            this.startSessionUrl = this.$el.dataset.startSessionUrl;
            this.heartbeatUrl = this.$el.dataset.heartbeatUrl;
            this.queueMemory = Object.create(null);
            this.queueDispatching = Object.create(null);
            const queueMessages = this.$el.dataset;
            const pageMessages = document.body?.dataset || {};
            this.queueMessages = {
                pending: queueMessages.progressQueuePending || pageMessages.progressQueuePending || '',
                storage: queueMessages.progressQueueStorage || pageMessages.progressQueueStorage || '',
                limit: queueMessages.progressQueueLimit || pageMessages.progressQueueLimit || '',
                terminal: queueMessages.progressQueueTerminal || pageMessages.progressQueueTerminal || '',
                account: queueMessages.progressQueueAccount || pageMessages.progressQueueAccount || '',
                activity: queueMessages.progressQueueActivity || pageMessages.progressQueueActivity || '',
            };
            if (!this.startSessionUrl || !this.heartbeatUrl) {
                console.error('progressTracker: missing start-session-url or heartbeat-url data attributes');
                return;
            }
            if (this.studentId) {
                const accountMarker = 'lms:progress:active-account';
                try {
                    localStorage.setItem(accountMarker, this.studentId);
                } catch (_) { /* queue writes below report the storage fallback */ }
                this.accountStorageHandler = event => {
                    if (
                        event.key === accountMarker
                        && event.newValue !== this.studentId
                    ) {
                        this.persistProgressAccumulation(true);
                        this.accountSuspended = true;
                        if (
                            this.queueStatusMessage !== this.queueMessages.storage
                            && this.queueStatusMessage !== this.queueMessages.limit
                        ) this.showProgressQueueMessage('account');
                    }
                };
                window.addEventListener('storage', this.accountStorageHandler);
                this.reconcilePendingProgressQueues();
            }
            this.progressUpdateHandler = event => {
                const detail = event.detail || {};
                if (
                    String(detail.lessonId) !== String(this.lessonId)
                    || String(detail.partId) !== String(this.partId)
                ) return;
                this.setProgressPercent(detail.percent);
            };
            window.addEventListener('lecture-progress-updated', this.progressUpdateHandler);
            // Paired audio/video of one lesson part share one backend progress
            // row. Mirror the seek slider between them so both stay aligned.
            this.positionHandler = event => this.applyPeerPosition(event.detail || {});
            window.addEventListener('lecture-position-updated', this.positionHandler);
            this.mediaAuthErrorHandler = event => {
                const detail = event.detail || {};
                const force = Boolean(detail.force);
                if (
                    !force
                    && detail.sourceUrl
                    && this.mediaUrl
                    && detail.sourceUrl !== this.mediaUrl
                ) return;
                if (this.destroyed || this.finalHeartbeatSent) return;
                this.mediaRecoveryPending = true;
                if (force) {
                    this.mediaRefreshFailures = 0;
                    this.mediaRecoverySessionId = null;
                } else if (this.sessionId && this.mediaRecoverySessionId === this.sessionId) {
                    return;
                }
                if (this.mediaRefreshInFlight) return;
                this.mediaRecoverySessionId = this.sessionId || '__pending__';
                this.refreshMediaSession({force});
            };
            this.$el.addEventListener('media-auth-expired', this.mediaAuthErrorHandler);
            this.onlineHandler = () => {
                if (this.destroyed) return;
                this.reconcilePendingProgressQueues();
                if (this.sessionId && !this.finalHeartbeatSent) this.sendHeartbeat();
                if (this.finalHeartbeatSent) return;
                if (this.mediaRecoveryPending) {
                    this.mediaRefreshFailures = 0;
                    this.refreshMediaSession({force: true});
                    return;
                }
                if (this.gateRefreshUrl && Date.parse(this.gateRefreshAt) <= Date.now()) {
                    this.refreshMediaGate({force: true});
                    return;
                }
                if (this.gateRefreshUrl) this.scheduleMediaRefresh();
            };
            window.addEventListener('online', this.onlineHandler);
            this.refreshMediaSession({initial: true, force: true});
            this.interval = setInterval(() => this.sendHeartbeat(), PROGRESS_HEARTBEAT_INTERVAL_MS);
            this.pageExitHandler = () => {
                this.resetPlaybackSample();
                this.cancelMediaRefresh();
                this.sendHeartbeat(true);
            };
            window.addEventListener('pagehide', this.pageExitHandler);
            this.visibilityHandler = () => {
                this.resetPlaybackSample();
                if (document.hidden) {
                    this.sendHeartbeat();
                    if (this.gateRefreshUrl) this.cancelMediaRefresh();
                } else if (this.mediaRecoveryPending && !this.mediaRefreshInFlight) {
                    this.mediaRefreshFailures = 0;
                    this.refreshMediaSession({force: true});
                } else if (this.gateRefreshUrl && !this.mediaRefreshInFlight) {
                    if (Date.parse(this.gateRefreshAt) <= Date.now()) {
                        this.refreshMediaGate({force: true});
                    } else {
                        this.scheduleMediaRefresh();
                    }
                }
            };
            document.addEventListener('visibilitychange', this.visibilityHandler);
        },

        destroy() {
            this.destroyed = true;
            if (this.interval) clearInterval(this.interval);
            this.cancelMediaRefresh();
            if (this.pageExitHandler) window.removeEventListener('pagehide', this.pageExitHandler);
            if (this.visibilityHandler) document.removeEventListener('visibilitychange', this.visibilityHandler);
            if (this.onlineHandler) window.removeEventListener('online', this.onlineHandler);
            if (this.progressUpdateHandler) {
                window.removeEventListener('lecture-progress-updated', this.progressUpdateHandler);
            }
            if (this.positionHandler) {
                window.removeEventListener('lecture-position-updated', this.positionHandler);
            }
            if (this.accountStorageHandler) {
                window.removeEventListener('storage', this.accountStorageHandler);
            }
            if (this.mediaAuthErrorHandler) {
                this.$el.removeEventListener('media-auth-expired', this.mediaAuthErrorHandler);
            }
            this.gateRefreshUrl = null;
            this.gateToken = null;
            this.sendHeartbeat(true);
        },

        applyPeerPosition(detail) {
            if (detail.lessonId === undefined || detail.partId === undefined) return;
            if (
                String(detail.lessonId) !== String(this.lessonId)
                || String(detail.partId) !== String(this.partId)
            ) return;
            const media = this.$el.querySelector('video, audio');
            if (!media || media === detail.element) return;
            // Never disturb a peer that is actually playing; only align the
            // slider of an idle pair.
            if (media.paused !== true) return;
            const target = Number(detail.time);
            if (!Number.isFinite(target) || target < 0) return;
            const current = Number(media.currentTime) || 0;
            if (Math.abs(current - target) <= 2) return;
            this.suppressPositionBroadcastUntil = performance.now() + 700;
            try {
                const player = media.player;
                if (player && typeof player.currentTime === 'function') player.currentTime(target);
                else media.currentTime = target;
            } catch (_) { /* a source swap may own the element; skip this tick */ }
        },

        broadcastPosition(media) {
            if (!media || !Number.isFinite(media.currentTime)) return;
            const now = performance.now();
            if (now < this.suppressPositionBroadcastUntil) return;
            if (now - this.lastPositionBroadcastAt < 800) return;
            this.lastPositionBroadcastAt = now;
            window.dispatchEvent(new CustomEvent('lecture-position-updated', {
                detail: {
                    lessonId: this.lessonId,
                    partId: this.partId,
                    time: media.currentTime,
                    element: media,
                },
            }));
        },

        applyResumePosition(media) {
            if (!media || this.resumeApplied || this.resumeSeconds <= 0) return;
            const target = this.resumeSeconds;
            let attempts = 0;
            const maxAttempts = 4;
            const giveUpAt = Date.now() + 9000;
            const readPosition = () => {
                try {
                    const player = media.player;
                    if (player && typeof player.currentTime === 'function') {
                        return Number(player.currentTime());
                    }
                    return Number(media.currentTime);
                } catch (_) {
                    return NaN;
                }
            };
            const isSettled = () => {
                const current = readPosition();
                return Number.isFinite(current) && current >= target - 2;
            };
            const stopListening = () => {
                media.removeEventListener('loadedmetadata', seek);
                media.removeEventListener('canplay', seek);
            };
            const latch = (warn) => {
                this.resumeApplied = true;
                stopListening();
                if (warn) {
                    console.warn('progressTracker: resume seek did not settle', {
                        target: target,
                        attempts: attempts,
                    });
                }
            };
            const seek = () => {
                if (this.resumeApplied || this.destroyed) return;
                if (isSettled()) {
                    latch(false);
                    return;
                }
                const seeking = Boolean(media.seeking);
                if (Date.now() > giveUpAt || (attempts >= maxAttempts && !seeking)) {
                    latch(true);
                    return;
                }
                // A seeking tech is already working toward the point: issuing
                // another seek would abort its in-flight segment load (this is
                // what strands far resumes at 00:00 on slow networks). Only
                // re-issue when the tech is idle; otherwise wait for it.
                if (!seeking) {
                    attempts += 1;
                    try {
                        const player = media.player;
                        const hasPlayer = player && typeof player.currentTime === 'function';
                        const current = hasPlayer ? Number(player.currentTime()) : Number(media.currentTime);
                        // Only jump forward to the saved point; never rewind
                        // an already-positioned player.
                        if (!Number.isFinite(current) || current < target - 1) {
                            if (hasPlayer) player.currentTime(target);
                            else media.currentTime = target;
                        }
                    } catch (_) { /* tech may still be settling */ }
                }
                // The first metadata event can precede a seekable tech (the
                // tracker sets source.src and Video.js then loads the same URL
                // through its own tech). Re-enter shortly after: it latches
                // when settled, warns when out of time/attempts, and otherwise
                // chains the next check.
                window.setTimeout(() => {
                    if (!this.resumeApplied && !this.destroyed) seek();
                }, seeking ? 900 : 600);
            };
            media.addEventListener('loadedmetadata', seek);
            media.addEventListener('canplay', seek);
            window.setTimeout(seek, 2000);
        },

        async startSession({refresh = false, forceReload = false} = {}) {
            const previousSessionId = this.sessionId;
            if (previousSessionId) {
                this.persistProgressAccumulation(true);
                this.dispatchProgressQueue(this.progressQueueKeyFor(previousSessionId));
            }
            const response = await fetch(this.startSessionUrl, {
                credentials: 'same-origin',
                headers: {Accept: 'application/json'},
            });
            const contentType = response.headers?.get('content-type') || '';
            if (!response.ok || (contentType && !contentType.includes('application/json'))) {
                const err = new Error(`Viewing session request failed: ${response.status}`);
                err.status = response.status;
                throw err;
            }
            const data = await response.json();
            if (this.destroyed || this.finalHeartbeatSent) return;
            let gateRefreshUrl = null;
            if (data.gate_url) {
                const refreshAt = Date.parse(data.gate_refresh_at);
                const validUntil = Date.parse(data.gate_valid_until);
                if (!data.gate_token || !Number.isFinite(refreshAt) || !Number.isFinite(validUntil) || validUntil <= Date.now()) {
                    throw new Error('Media gate start response was incomplete.');
                }
                const separator = data.gate_url.includes('?') ? '&' : '?';
                gateRefreshUrl = `${data.gate_url}${separator}session_id=${encodeURIComponent(data.session_id)}&token=${encodeURIComponent(data.token)}`;
            }
            const hadSession = Boolean(this.sessionId);
            this.sessionId = data.session_id;
            if (previousSessionId && previousSessionId !== data.session_id) {
                this.closeProgressQueueSession(previousSessionId);
            }
            this.heartbeatSequence = 0;
            this.queueEntry = this.studentId
                ? this.ensureProgressQueueEntry(data.session_id)
                : null;
            this.reconcilePendingProgressQueues();
            if (Number.isFinite(data.segment_token_lifetime_seconds) && data.segment_token_lifetime_seconds > 0) {
                this.segmentTokenLifetimeSeconds = data.segment_token_lifetime_seconds;
            }
            if (typeof data.progress_percent === 'number' && Number.isFinite(data.progress_percent)) {
                this.setProgressPercent(data.progress_percent);
                this.publishProgress();
            }
            if (typeof data.resume_seconds === 'number' && Number.isFinite(data.resume_seconds)) {
                this.resumeSeconds = Math.max(0, Math.floor(data.resume_seconds));
            }
            this.mediaUrl = `${data.manifest_url}?session_id=${encodeURIComponent(data.session_id)}&token=${encodeURIComponent(data.token)}`;
            this.gateRefreshUrl = gateRefreshUrl;
            this.gateToken = gateRefreshUrl ? data.gate_token : null;
            this.gateRefreshAt = gateRefreshUrl ? data.gate_refresh_at : null;
            this.gateValidUntil = gateRefreshUrl ? data.gate_valid_until : null;
            this.mediaRefreshFailures = 0;
            this.mediaRecoveryPending = false;
            this.mediaRecoverySessionId = null;
            const media = this.$el.querySelector('video, audio');
            const source = media?.querySelector('source');
            if (media && source) {
                // On the very first session, continue from the last verified
                // second. Token refreshes keep the live position instead.
                if (!refresh && !hadSession) this.applyResumePosition(media);
                source.src = this.mediaUrl;
                media.dispatchEvent(new CustomEvent('media-session-ready', {
                    bubbles: true,
                    detail: {
                        element: media,
                        url: this.mediaUrl,
                        gateToken: this.gateToken,
                        refresh: refresh || hadSession,
                        forceReload: Boolean(forceReload || refresh || hadSession),
                    },
                }));
            }
            this.scheduleMediaRefresh();
        },

        scheduleMediaRefresh(delayOverrideMs = null, gateRefresh = null) {
            this.cancelMediaRefresh();
            if (this.destroyed || this.finalHeartbeatSent) return;
            if (!this.sessionId) return;
            const useGateRefresh = gateRefresh === null
                ? delayOverrideMs === null && Boolean(this.gateRefreshUrl)
                : gateRefresh;
            if (useGateRefresh && document.hidden) return;
            let refreshDelay;
            if (useGateRefresh) {
                const refreshAt = Date.parse(this.gateRefreshAt);
                const validUntil = Date.parse(this.gateValidUntil);
                if (!Number.isFinite(refreshAt) || !Number.isFinite(validUntil)) return;
                const jitter = Math.random() * MEDIA_GATE_REFRESH_JITTER_MS - MEDIA_GATE_REFRESH_EARLY_MS;
                const latestRefresh = validUntil - 30000;
                refreshDelay = Math.max(1000, Math.min(refreshAt + jitter, latestRefresh) - Date.now());
            } else {
                refreshDelay = delayOverrideMs !== null
                    ? Math.max(1000, delayOverrideMs)
                    : Math.max(
                        30000,
                        Math.floor(this.segmentTokenLifetimeSeconds * 1000 * 0.8),
                    );
            }
            this.mediaRefreshTimer = setTimeout(
                () => useGateRefresh
                    ? this.refreshMediaGate()
                    : this.refreshMediaSession({force: delayOverrideMs !== null}),
                refreshDelay,
            );
        },

        cancelMediaRefresh() {
            if (this.mediaRefreshTimer) clearTimeout(this.mediaRefreshTimer);
            this.mediaRefreshTimer = null;
        },

        async refreshMediaSession({initial = false, force = false} = {}) {
            if (this.destroyed || this.finalHeartbeatSent || this.mediaRefreshInFlight) return;
            if (!initial && !force && !this.sessionId) return;
            const media = this.$el.querySelector('video, audio');
            // Avoid flushing active playback buffers on proactive 80% renewals;
            // wait until paused or wait for expiry/error recovery to renew cleanly.
            if (
                !initial
                && !force
                && !this.mediaRecoveryPending
                && media
                && !media.paused
                && !media.ended
                && !media.error
            ) {
                this.scheduleMediaRefresh(60000, false);
                return;
            }
            if (this.mediaRecoveryPending && media) {
                media.dispatchEvent(new CustomEvent('media-recovery-starting', {
                    bubbles: true,
                    detail: {element: media},
                }));
            }
            this.cancelMediaRefresh();
            this.mediaRefreshInFlight = true;
            const shouldRefreshPosition = !initial && Boolean(this.sessionId);
            try {
                await this.startSession({
                    refresh: shouldRefreshPosition,
                    forceReload: shouldRefreshPosition || this.mediaRecoveryPending,
                });
            } catch (error) {
                console.warn('progressTracker: media token refresh failed', error);
                this.mediaRecoverySessionId = null;
                this.mediaRefreshFailures += 1;
                if (this.mediaRefreshFailures <= MAX_MEDIA_RECOVERY_RETRIES) {
                    const backoffMs = Math.min(
                        MEDIA_RECOVERY_MAX_DELAY_MS,
                        MEDIA_RECOVERY_BASE_DELAY_MS * Math.pow(2, this.mediaRefreshFailures - 1),
                    );
                    this.scheduleMediaRefresh(backoffMs, false);
                } else if (media) {
                    media.dispatchEvent(new CustomEvent('media-recovery-failed', {
                        bubbles: true,
                        detail: {element: media, sourceUrl: this.mediaUrl},
                    }));
                }
            } finally {
                this.mediaRefreshInFlight = false;
            }
        },

        async refreshMediaGate({force = false} = {}) {
            if (
                this.destroyed
                || this.finalHeartbeatSent
                || this.mediaRefreshInFlight
                || !this.sessionId
                || !this.gateRefreshUrl
            ) return;
            if (document.hidden && !force) {
                this.cancelMediaRefresh();
                return;
            }
            this.cancelMediaRefresh();
            this.mediaRefreshInFlight = true;
            const media = this.$el.querySelector('video, audio');
            try {
                const response = await fetch(this.gateRefreshUrl, {
                    credentials: 'same-origin',
                    cache: 'no-store',
                    headers: {Accept: 'application/json'},
                });
                if (response.status === 401 || response.status === 403) {
                    await this.startSession({refresh: true, forceReload: true});
                    return;
                }
                if (!response.ok) {
                    const error = new Error(`Media gate refresh failed: ${response.status}`);
                    error.status = response.status;
                    const retryAfter = Number(response.headers?.get('Retry-After'));
                    error.retryAfterMs = Number.isFinite(retryAfter) && retryAfter > 0
                        ? Math.min(60000, retryAfter * 1000)
                        : 0;
                    throw error;
                }
                const data = await response.json();
                const refreshAt = Date.parse(data.refresh_at);
                const validUntil = Date.parse(data.valid_until);
                if (!data.token || !Number.isFinite(refreshAt) || !Number.isFinite(validUntil) || validUntil <= Date.now()) {
                    throw new Error('Media gate refresh response was incomplete.');
                }
                const tokenChanged = data.token !== this.gateToken;
                this.gateToken = data.token;
                this.gateRefreshAt = data.refresh_at;
                this.gateValidUntil = data.valid_until;
                this.mediaRefreshFailures = 0;
                if (tokenChanged && media) {
                    media.dispatchEvent(new CustomEvent('media-gate-refresh', {
                        bubbles: true,
                        detail: {
                            element: media,
                            token: data.token,
                            tokenChanged: true,
                            url: this.mediaUrl,
                        },
                    }));
                }
                this.scheduleMediaRefresh();
            } catch (error) {
                console.warn('progressTracker: media gate refresh failed', error);
                this.mediaRefreshFailures += 1;
                if (this.mediaRefreshFailures <= MAX_MEDIA_RECOVERY_RETRIES) {
                    const backoffMs = Math.max(
                        error.retryAfterMs || 0,
                        Math.min(
                            MEDIA_RECOVERY_MAX_DELAY_MS,
                            MEDIA_RECOVERY_BASE_DELAY_MS * Math.pow(2, this.mediaRefreshFailures - 1),
                        ),
                    );
                    this.scheduleMediaRefresh(backoffMs, true);
                } else if (media) {
                    media.dispatchEvent(new CustomEvent('media-recovery-failed', {
                        bubbles: true,
                        detail: {element: media, sourceUrl: this.mediaUrl},
                    }));
                }
            } finally {
                this.mediaRefreshInFlight = false;
            }
        },

        setProgressPercent(value) {
            if (typeof value !== 'number' || !Number.isFinite(value)) return;
            this.progressPercent = Math.min(Math.max(Math.round(value), 0), 100);
        },

        publishProgress() {
            window.dispatchEvent(new CustomEvent('lecture-progress-updated', {
                detail: {
                    lessonId: this.lessonId,
                    partId: this.partId,
                    percent: this.progressPercent,
                },
            }));
        },

        onTimeUpdate(event) {
            const media = event.currentTarget || event.target;
            if (!media || !Number.isFinite(media.currentTime)) return;

            this.samplePlayback(media);
            this.broadcastPosition(media);

            if (media.played && media.played.length) {
                const ranges = [];
                for (let index = 0; index < media.played.length; index += 1) {
                    const start = media.played.start(index);
                    const end = media.played.end(index);
                    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
                        ranges.push([start, end]);
                    }
                }
                this.playedRanges = ranges;
            } else if (!media.paused) {
                // Some browsers report an empty played list while playing.
                // Never synthesise a range for a paused player (e.g. a seek on
                // the idle paired audio/video track): a paused seek plays
                // nothing and must not be credited.
                this.playedRanges = [[media.currentTime, media.currentTime + 5]];
            }
            this.persistProgressAccumulation(false);
        },

        onPlay(event) {
            const media = event.currentTarget || event.target;
            this.playbackSample = media ? {at: performance.now(), time: media.currentTime} : null;
        },

        onPause() {
            this.resetPlaybackSample();
            this.sendHeartbeat();
        },

        onSeek() {
            this.resetPlaybackSample();
            this.sendHeartbeat();
        },

        resetPlaybackSample() {
            this.playbackSample = null;
        },

        samplePlayback(media) {
            const now = performance.now();
            const visibleAndPlaying = !document.hidden && !media.paused && !media.seeking;
            if (!visibleAndPlaying) {
                this.playbackSample = null;
                return;
            }
            const current = {at: now, time: media.currentTime};
            const previous = this.playbackSample;
            this.playbackSample = current;
            if (!previous) return;
            const elapsed = (now - previous.at) / 1000;
            const playbackDelta = current.time - previous.time;
            if (
                elapsed <= 2.5
                && elapsed > 0
                && playbackDelta > 0
                && playbackDelta / elapsed >= 0.25
                && playbackDelta / elapsed <= 4
            ) {
                this.activeSecondsBuffer += Math.min(elapsed, 2.5);
            }
        },

        sendHeartbeat(ending = false) {
            if (!this.sessionId || !this.studentId || this.accountSuspended) return;
            if (ending && this.finalHeartbeatSent) return;
            if (ending) this.finalHeartbeatSent = true;
            this.persistProgressAccumulation(true, ending);
            this.dispatchProgressQueue(this.progressQueueKeyFor(this.sessionId), {keepalive: ending});
        },

        showProgressQueueMessage(name) {
            this.queueStatusMessage = this.queueMessages?.[name] || '';
        },

        progressQueueAccountPrefix() {
            return `${PROGRESS_QUEUE_PREFIX}:${encodeURIComponent(String(this.studentId))}:`;
        },

        progressQueueKeyFor(sessionId) {
            return progressQueueKey(this.studentId, this.lessonId, this.partId, sessionId);
        },

        progressQueueBytes(value) {
            if (typeof TextEncoder === 'function') return new TextEncoder().encode(value).length;
            return String(value).length * 2;
        },

        progressQueueStorageRecords() {
            const prefix = this.progressQueueAccountPrefix();
            const records = Object.create(null);
            try {
                for (let index = 0; index < localStorage.length; index += 1) {
                    const key = localStorage.key(index);
                    if (key && key.startsWith(prefix)) records[key] = localStorage.getItem(key) || '';
                }
            } catch (_) {
                return null;
            }
            Object.keys(this.queueMemory || {}).forEach(key => {
                if (key.startsWith(prefix)) records[key] = JSON.stringify(this.queueMemory[key]);
            });
            return records;
        },

        readProgressQueue(key) {
            if (this.queueMemory?.[key]) {
                if (progressQueueEntryMatchesKey(this.queueMemory[key], key, this.studentId)) return this.queueMemory[key];
                this.showProgressQueueMessage('terminal');
                return null;
            }
            try {
                const raw = localStorage.getItem(key);
                if (raw !== null) {
                    const entry = JSON.parse(raw);
                    if (progressQueueEntryMatchesKey(entry, key, this.studentId)) return entry;
                    this.showProgressQueueMessage('terminal');
                    return null;
                }
            } catch (_) {
                this.showProgressQueueMessage('storage');
            }
            if (this.queueEntry?.key === key) return this.queueEntry;
            return null;
        },

        writeProgressQueue(entry) {
            const key = entry.key;
            const serialized = JSON.stringify(entry);
            const records = this.progressQueueStorageRecords();
            if (records) {
                records[key] = serialized;
                const keys = Object.keys(records);
                const bytes = keys.reduce(
                    (total, recordKey) => total + this.progressQueueBytes(recordKey) + this.progressQueueBytes(records[recordKey]),
                    0,
                );
                if (keys.length > PROGRESS_QUEUE_MAX_ENTRIES || bytes > PROGRESS_QUEUE_MAX_BYTES) {
                    this.showProgressQueueMessage('limit');
                    return false;
                }
                try {
                    localStorage.setItem(key, serialized);
                    delete this.queueMemory[key];
                    if (this.queueStatusMessage === this.queueMessages.storage) this.queueStatusMessage = '';
                    return true;
                } catch (_) { /* preserve a bounded in-memory copy below */ }
            }

            const memoryRecords = records || Object.create(null);
            Object.keys(this.queueMemory || {}).forEach(memoryKey => {
                if (memoryKey.startsWith(this.progressQueueAccountPrefix())) {
                    memoryRecords[memoryKey] = JSON.stringify(this.queueMemory[memoryKey]);
                }
            });
            memoryRecords[key] = serialized;
            const memoryKeys = Object.keys(memoryRecords);
            const memoryBytes = memoryKeys.reduce(
                (total, memoryKey) => total + this.progressQueueBytes(memoryKey) + this.progressQueueBytes(memoryRecords[memoryKey]),
                0,
            );
            if (memoryKeys.length > PROGRESS_QUEUE_MAX_ENTRIES || memoryBytes > PROGRESS_QUEUE_MAX_BYTES) {
                this.showProgressQueueMessage('limit');
                return false;
            }
            this.queueMemory[key] = entry;
            this.showProgressQueueMessage('storage');
            return true;
        },

        removeProgressQueue(key) {
            delete this.queueMemory[key];
            try {
                localStorage.removeItem(key);
            } catch (_) { /* an in-memory queue can still be removed */ }
            if (this.queueEntry?.key === key) this.queueEntry = null;
            if (!Object.keys(this.queueMemory || {}).length) this.queueStatusMessage = '';
        },

        ensureProgressQueueEntry(sessionId) {
            if (!this.studentId || !sessionId) return null;
            const key = this.progressQueueKeyFor(sessionId);
            const existing = this.readProgressQueue(key);
            if (existing) return existing;
            return {
                version: 1,
                key,
                studentId: String(this.studentId),
                lessonId: String(this.lessonId),
                partId: String(this.partId),
                sessionId: String(sessionId),
                nextSequence: 1,
                pending: null,
                bufferedRanges: [],
                bufferedActiveSeconds: 0,
                bufferedEnded: false,
                terminal: false,
                closed: false,
                updatedAt: Date.now(),
            };
        },

        progressQueueHasWork(entry) {
            return Boolean(
                entry?.pending
                || entry?.bufferedEnded
                || (entry?.bufferedRanges || []).length
                || Number(entry?.bufferedActiveSeconds) > 0
            );
        },

        persistProgressAccumulation(force = false, ending = false) {
            if (!this.studentId || !this.sessionId || this.accountSuspended) return false;
            const hasInput = this.playedRanges.length || this.activeSecondsBuffer > 0 || ending;
            if (!hasInput) return true;
            const now = Date.now();
            if (!force && now - this.queueLastPersistAt < 1000) return true;
            const entry = this.ensureProgressQueueEntry(this.sessionId);
            if (!entry || entry.terminal) return false;
            const candidate = {
                ...entry,
                bufferedRanges: progressQueueNormalizeRanges([
                    ...(entry.bufferedRanges || []),
                    ...this.playedRanges,
                ]),
                bufferedActiveSeconds: Math.round((
                    Math.max(0, Number(entry.bufferedActiveSeconds) || 0)
                    + Math.max(0, Number(this.activeSecondsBuffer) || 0)
                ) * 100) / 100,
                bufferedEnded: Boolean(entry.bufferedEnded || ending),
                updatedAt: now,
            };
            if (!this.writeProgressQueue(candidate)) return false;
            this.queueEntry = candidate;
            this.playedRanges = [];
            this.activeSecondsBuffer = 0;
            this.queueLastPersistAt = now;
            return true;
        },

        currentPlaybackRate() {
            const media = this.$el?.querySelector?.('video, audio');
            const rate = Number(media?.playbackRate ?? 1);
            if (!Number.isFinite(rate) || rate <= 0) return 1;
            return Math.min(2, Math.max(1, Math.round(rate * 100) / 100));
        },

        buildProgressQueuePending(entry) {
            if (entry.pending || entry.terminal) return entry;
            const ranges = progressQueueNormalizeRanges(entry.bufferedRanges || []);
            const requestRanges = ranges.slice(0, PROGRESS_QUEUE_MAX_RANGES);
            const remainingRanges = ranges.slice(PROGRESS_QUEUE_MAX_RANGES);
            const bufferedActive = Math.max(0, Number(entry.bufferedActiveSeconds) || 0);
            const activeSeconds = Math.min(3600, bufferedActive);
            const remainingActive = Math.round((bufferedActive - activeSeconds) * 100) / 100;
            const hasMore = remainingRanges.length > 0 || remainingActive > 0;
            const ended = Boolean(entry.bufferedEnded && !hasMore);
            if (!requestRanges.length && activeSeconds <= 0 && !ended) return entry;
            const sequence = Math.max(1, Number(entry.nextSequence) || 1);
            const payload = {
                session_id: entry.sessionId,
                ranges: requestRanges,
                ended,
                active_seconds: activeSeconds,
                playback_rate: this.currentPlaybackRate(),
                sequence,
            };
            return {
                ...entry,
                pending: {payload, fingerprint: JSON.stringify(payload)},
                bufferedRanges: remainingRanges,
                bufferedActiveSeconds: remainingActive,
                bufferedEnded: Boolean(entry.bufferedEnded && !ended),
                updatedAt: Date.now(),
            };
        },

        closeProgressQueueSession(sessionId) {
            const key = this.progressQueueKeyFor(sessionId);
            const entry = this.readProgressQueue(key);
            if (!entry) return;
            const closed = {...entry, closed: true, updatedAt: Date.now()};
            if (this.progressQueueHasWork(closed)) this.writeProgressQueue(closed);
            else this.removeProgressQueue(key);
        },

        async reconcilePendingProgressQueues() {
            if (!this.studentId || this.accountSuspended) return;
            const prefix = this.progressQueueAccountPrefix();
            const keys = Object.create(null);
            try {
                for (let index = 0; index < localStorage.length; index += 1) {
                    const key = localStorage.key(index);
                    if (key && key.startsWith(prefix)) keys[key] = true;
                }
            } catch (_) { /* use this tab's bounded in-memory fallback */ }
            Object.keys(this.queueMemory || {}).forEach(key => {
                if (key.startsWith(prefix)) keys[key] = true;
            });
            for (const key of Object.keys(keys)) {
                const entry = this.readProgressQueue(key);
                if (!entry || entry.terminal) continue;
                if (!this.progressQueueHasWork(entry)) {
                    if (entry.closed || entry.sessionId !== this.sessionId) this.removeProgressQueue(key);
                    continue;
                }
                await this.dispatchProgressQueue(key);
            }
        },

        async dispatchProgressQueue(key, options = {}) {
            if (!this.studentId || !key?.startsWith(this.progressQueueAccountPrefix()) || this.accountSuspended) return;
            if (this.queueDispatching[key]) return;
            this.queueDispatching[key] = true;
            let moreWork = false;
            try {
                const dispatch = async () => {
                    moreWork = await this.dispatchProgressQueueLocked(key, options);
                };
                if (typeof navigator !== 'undefined' && navigator.locks?.request) {
                    await navigator.locks.request(`lms-progress:${key}`, dispatch);
                } else {
                    await dispatch();
                }
            } catch (_) {
                this.showProgressQueueMessage('pending');
            } finally {
                delete this.queueDispatching[key];
            }
            if (moreWork && !options.keepalive) {
                window.setTimeout(() => this.dispatchProgressQueue(key), 0);
            }
        },

        async dispatchProgressQueueLocked(key, options = {}) {
            let entry = this.readProgressQueue(key);
            if (!entry || entry.terminal) return false;
            if (!entry.pending) {
                entry = this.buildProgressQueuePending(entry);
                if (!entry.pending) return false;
                if (!this.writeProgressQueue(entry)) return false;
            }
            const pending = entry.pending;
            const fingerprint = pending.fingerprint || JSON.stringify(pending.payload);
            let response;
            try {
                response = await fetch(this.heartbeatUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRF()},
                    body: JSON.stringify(pending.payload),
                    keepalive: Boolean(options.keepalive),
                });
            } catch (_) {
                this.showProgressQueueMessage('pending');
                return false;
            }
            if (!response.ok) {
                if ([400, 401, 403, 404, 410].includes(response.status)) {
                    const latest = this.readProgressQueue(key);
                    if (latest?.pending?.fingerprint === fingerprint) {
                        latest.terminal = true;
                        latest.terminalReason = `http_${response.status}`;
                        latest.updatedAt = Date.now();
                        this.writeProgressQueue(latest);
                    }
                    this.showProgressQueueMessage('terminal');
                } else {
                    this.showProgressQueueMessage('pending');
                }
                return false;
            }

            let acknowledgement;
            try {
                acknowledgement = await response.json();
            } catch (_) {
                this.showProgressQueueMessage('pending');
                return false;
            }
            if (acknowledgement.status === 'rejected' && acknowledgement.terminal) {
                const latest = this.readProgressQueue(key);
                if (latest?.pending?.fingerprint === fingerprint) {
                    latest.terminal = true;
                    latest.terminalReason = acknowledgement.reason || 'rejected';
                    latest.updatedAt = Date.now();
                    this.writeProgressQueue(latest);
                }
                this.showProgressQueueMessage('terminal');
                return false;
            }
            const reconciled = progressQueueReconcilePending(pending, acknowledgement);
            if (!reconciled) {
                this.showProgressQueueMessage('pending');
                return false;
            }
            const latest = this.readProgressQueue(key);
            if (!latest || latest.pending?.fingerprint !== fingerprint) return this.progressQueueHasWork(latest);
            latest.pending = null;
            latest.nextSequence = Math.max(1, reconciled.nextSequence);
            latest.bufferedRanges = progressQueueNormalizeRanges([
                ...reconciled.remainingRanges,
                ...(latest.bufferedRanges || []),
            ]);
            latest.bufferedActiveSeconds = Math.round((
                Math.max(0, Number(latest.bufferedActiveSeconds) || 0)
                + reconciled.remainingActiveSeconds
            ) * 100) / 100;
            latest.bufferedEnded = Boolean(latest.bufferedEnded || reconciled.ended);
            latest.closed = Boolean(latest.closed || (
                reconciled.ended
                && !latest.bufferedRanges.length
                && latest.bufferedActiveSeconds <= 0
                && !latest.bufferedEnded
            ));
            latest.updatedAt = Date.now();
            if (
                String(latest.lessonId) === String(this.lessonId)
                && String(latest.partId) === String(this.partId)
                && Number.isFinite(acknowledgement.percent)
            ) {
                this.setProgressPercent(acknowledgement.percent);
                this.publishProgress();
            }
            if (reconciled.unreconciledActiveSeconds > 0) this.showProgressQueueMessage('activity');
            if (!this.progressQueueHasWork(latest)) {
                if (latest.closed || latest.sessionId !== this.sessionId) {
                    this.removeProgressQueue(key);
                    return false;
                }
                this.queueEntry = latest;
                this.writeProgressQueue(latest);
                if (reconciled.unreconciledActiveSeconds <= 0) this.queueStatusMessage = '';
                return false;
            }
            if (!this.writeProgressQueue(latest)) return false;
            if (latest.sessionId === this.sessionId) {
                this.queueEntry = latest;
                this.heartbeatSequence = Math.max(this.heartbeatSequence, latest.nextSequence - 1);
            }
            this.showProgressQueueMessage('pending');
            return true;
        },

        getCSRF() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        }
    };
}
