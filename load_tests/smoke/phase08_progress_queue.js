const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.resolve(
    __dirname,
    '..',
    '..',
    'management_system',
    'static',
    'js',
    'alpine',
    'components',
    'progressTracker.js',
);
const source = fs.readFileSync(sourcePath, 'utf8');
const storedEntries = new Map();
const context = vm.createContext({
    document: {querySelector: () => null},
    localStorage: {
        get length() { return storedEntries.size; },
        key: index => Array.from(storedEntries.keys())[index] || null,
        getItem: key => storedEntries.has(key) ? storedEntries.get(key) : null,
        setItem: (key, value) => storedEntries.set(key, value),
        removeItem: key => storedEntries.delete(key),
    },
});
vm.runInContext(`${source}\nthis.queueChecks = {
    normalize: progressQueueNormalizeRanges,
    reconcile: progressQueueReconcilePending,
    key: progressQueueKey,
    tracker: progressTracker,
};`, context);

assert.equal(
    JSON.stringify(context.queueChecks.normalize([[0, 1], [0.5, 2], [3, 4]])),
    JSON.stringify([[0, 2], [3, 4]]),
);
assert.equal(
    context.queueChecks.key('12', '34', '56', 'session-a'),
    'lms:progress:v1:12:34:56:session-a',
);

const pending = {
    payload: {
        session_id: 'session-a',
        sequence: 3,
        ranges: [[0, 4], [6, 8]],
        active_seconds: 7.5,
        ended: false,
    },
};
const partial = context.queueChecks.reconcile(pending, {
    status: 'ok',
    session_id: 'session-a',
    sequence: 3,
    accepted_ranges: [[0, 1]],
    already_saved_ranges: [[1, 2]],
    credited_active_seconds: 3,
});
assert.equal(JSON.stringify(partial.remainingRanges), JSON.stringify([[2, 4], [6, 8]]));
assert.equal(partial.remainingActiveSeconds, 0);
assert.equal(partial.unreconciledActiveSeconds, 4.5);
assert.equal(partial.nextSequence, 4);

const resync = context.queueChecks.reconcile(pending, {
    status: 'resync',
    reason: 'stale_sequence',
    session_id: 'session-a',
    sequence: 3,
    last_sequence: 4,
    already_saved_ranges: [[0, 2]],
});
assert.equal(JSON.stringify(resync.remainingRanges), JSON.stringify([[2, 4], [6, 8]]));
assert.equal(resync.remainingActiveSeconds, 0);
assert.equal(resync.unreconciledActiveSeconds, 7.5);
assert.equal(resync.nextSequence, 5);

const replayActivity = context.queueChecks.reconcile(pending, {
    status: 'ok',
    reason: 'replay_activity_not_credited',
    session_id: 'session-a',
    sequence: 3,
    accepted_ranges: [[0, 4], [6, 8]],
    already_saved_ranges: [],
    credited_active_seconds: 0,
});
assert.equal(replayActivity.remainingActiveSeconds, 0);
assert.equal(replayActivity.unreconciledActiveSeconds, 7.5);

const previousKey = context.queueChecks.key('12', 'old-lesson', 'old-part', 'session-a');
storedEntries.set(previousKey, JSON.stringify({
    version: 1,
    key: previousKey,
    studentId: '12',
    lessonId: 'old-lesson',
    partId: 'old-part',
    sessionId: 'session-a',
    pending: {payload: pending.payload},
    bufferedRanges: [],
    bufferedActiveSeconds: 0,
    bufferedEnded: false,
}));
const tracker = context.queueChecks.tracker();
tracker.studentId = '12';
tracker.lessonId = 'current-lesson';
tracker.partId = 'current-part';
tracker.queueMemory = Object.create(null);
tracker.queueMessages = {terminal: 'invalid queue'};
tracker.sessionId = 'fresh-session';
tracker.heartbeatUrl = '/heartbeat';
tracker.queueDispatching = Object.create(null);
tracker.accountSuspended = false;
assert.equal(tracker.readProgressQueue(previousKey).sessionId, 'session-a');
const dispatched = [];
context.fetch = async (url, request) => {
    dispatched.push({url, payload: JSON.parse(request.body)});
    return {
        ok: true,
        json: async () => ({
            status: 'ok',
            session_id: 'session-a',
            sequence: 3,
            accepted_ranges: [[0, 4], [6, 8]],
            already_saved_ranges: [],
            credited_active_seconds: 7.5,
            percent: 50,
        }),
    };
};
storedEntries.set(previousKey, JSON.stringify({
    version: 1,
    key: previousKey,
    studentId: '12',
    lessonId: 'old-lesson',
    partId: 'old-part',
    sessionId: 'session-a',
    pending: {payload: pending.payload, fingerprint: JSON.stringify(pending.payload)},
    bufferedRanges: [],
    bufferedActiveSeconds: 0,
    bufferedEnded: false,
}));
const queueDispatchCheck = tracker.reconcilePendingProgressQueues().then(() => {
    assert.equal(dispatched.length, 1);
    assert.equal(dispatched[0].url, '/heartbeat');
    assert.equal(dispatched[0].payload.session_id, 'session-a');
    assert.equal(storedEntries.has(previousKey), false);
    console.log('Phase 8 browser queue contract checks passed.');
}).catch(error => {
    console.error(error);
    process.exitCode = 1;
});

for (const template of [
    'management_system/templates/partials/lesson_stream_content.html',
    'management_system/templates/student_next/partials/lesson_body.html',
]) {
    const markup = fs.readFileSync(path.resolve(__dirname, '..', '..', template), 'utf8');
    assert.match(markup, /data-student-id="\{% if request\.user\.is_authenticated/);
    assert.match(markup, /@seeked="onSeek"/);
    assert.match(markup, /x-show="queueStatusMessage"/);
}
for (const template of [
    'management_system/templates/base.html',
    'management_system/templates/student_next/base.html',
]) {
    const markup = fs.readFileSync(path.resolve(__dirname, '..', '..', template), 'utf8');
    assert.match(markup, /data-progress-queue-pending=/);
    assert.match(markup, /data-progress-queue-terminal=/);
}
const arabicCatalog = fs.readFileSync(
    path.resolve(__dirname, '..', '..', 'locale/ar/LC_MESSAGES/django.po'),
    'utf8',
);
for (const message of [
    'Saved progress is waiting to sync.',
    'This browser could not save progress for later. Keep this page open until it syncs.',
    'The progress queue on this device is full. Keep this page open and sync before closing it.',
    'Some progress could not be saved because this session can no longer accept it. Contact support if progress is missing.',
    'Progress syncing paused because this browser switched to another student account.',
    'Some activity time could not be confirmed and was not counted.',
]) assert.ok(arabicCatalog.includes(`msgid "${message}"`), `Missing Arabic catalog entry: ${message}`);

queueDispatchCheck;
