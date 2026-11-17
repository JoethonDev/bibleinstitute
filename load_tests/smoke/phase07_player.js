#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const trackerSource = fs.readFileSync(
    path.join(root, 'management_system/static/js/alpine/components/progressTracker.js'),
    'utf8',
);
const mediaSource = fs.readFileSync(
    path.join(root, 'management_system/static/js/lesson_media.js'),
    'utf8',
);

const OLD_TOKEN = 'OldToken01234567';
const NEW_TOKEN = 'NewToken01234567';
const NEXT_TOKEN = 'NextToken0123456';

function customEvent(type, options = {}) {
    return {type, detail: options.detail || {}, bubbles: Boolean(options.bubbles)};
}

async function checkTrackerRefreshesGate() {
    const timers = [];
    const fetchUrls = [];
    const events = [];
    const trackerContext = vm.createContext({
        document: {querySelector: () => null, hidden: false},
        window: {},
        CustomEvent: customEvent,
        setTimeout: (fn, delay) => {
            timers.push({fn, delay});
            return timers.length;
        },
        clearTimeout: () => {},
        fetch: async url => {
            fetchUrls.push(url);
            return {
                ok: true,
                status: 200,
                headers: {get: () => 'application/json'},
                json: async () => ({
                    token: NEW_TOKEN,
                    refresh_at: new Date(Date.now() + 60_000).toISOString(),
                    valid_until: new Date(Date.now() + 600_000).toISOString(),
                }),
            };
        },
        Date,
        Math,
        URLSearchParams,
        console,
    });
    vm.runInContext(`${trackerSource}\nthis.phase07Tracker = progressTracker;`, trackerContext);
    const tracker = trackerContext.phase07Tracker();
    const media = {dispatchEvent: event => events.push(event)};
    tracker.$el = {querySelector: selector => selector === 'video, audio' ? media : null};
    tracker.sessionId = 'session-a';
    tracker.gateRefreshUrl = '/gate/?session_id=session-a&token=session-signature';
    tracker.gateToken = OLD_TOKEN;
    tracker.gateRefreshAt = new Date(Date.now() + 30_000).toISOString();
    tracker.gateValidUntil = new Date(Date.now() + 300_000).toISOString();

    tracker.scheduleMediaRefresh();
    assert.equal(timers.length, 1, 'one timer should own edge-token refresh');
    assert.ok(timers[0].delay >= 1000 && timers[0].delay <= 180_000);

    await timers[0].fn();
    assert.equal(fetchUrls.length, 1, 'the scheduled timer should call the gate endpoint');
    assert.equal(tracker.gateToken, NEW_TOKEN, 'the real refresh stores the rotated token');
    assert.equal(events.length, 1, 'a changed token dispatches exactly one gate event');
    assert.equal(events[0].type, 'media-gate-refresh');
    assert.equal(events[0].detail.token, NEW_TOKEN);
    assert.equal(events[0].detail.tokenChanged, true);
    assert.equal(events[0].detail.element, media);
    assert.equal(timers.length, 2, 'a successful refresh re-arms the refresh timer');
    assert.ok(timers[1].delay >= 1000 && timers[1].delay <= 180_000);
}

function createNativeMedia(sourceUrl) {
    const listeners = Object.create(null);
    let loads = 0;
    let plays = 0;
    const source = {src: sourceUrl};
    const media = {
        tagName: 'VIDEO',
        currentTime: 100,
        paused: false,
        ended: false,
        controls: false,
        _lastKnownTime: 100,
        _mediaSourceUrl: null,
        classList: {remove: () => {}, add: () => {}},
        canPlayType: () => 'probably',
        querySelector: () => source,
        setAttribute: () => {},
        addEventListener: (name, handler) => {
            (listeners[name] = listeners[name] || []).push(handler);
        },
        removeEventListener: (name, handler) => {
            listeners[name] = (listeners[name] || []).filter(entry => entry !== handler);
        },
        dispatchEvent: event => {
            (listeners[event.type] || []).slice().forEach(handler => handler(event));
            return true;
        },
        load: () => { loads += 1; },
        play: async () => { plays += 1; },
    };
    return {
        media,
        source,
        listeners,
        loadCount: () => loads,
        playCount: () => plays,
    };
}

function createBrowserDocument(nativeMedia) {
    const documentHandlers = Object.create(null);
    const bodyHandlers = Object.create(null);
    return {
        documentHandlers,
        bodyHandlers,
        document: {
            body: {addEventListener: (name, handler) => { bodyHandlers[name] = handler; }},
            addEventListener: (name, handler) => { documentHandlers[name] = handler; },
            querySelectorAll: selector => selector === 'video, audio' ? [nativeMedia] : [],
        },
    };
}

function checkGateHookAndNativeFallback() {
    const canonicalUrl = 'https://app.example/manifest/?session_id=session-a&token=session-signature';
    const gateSegmentUrl = `https://cdn.example/g/${OLD_TOKEN}/course/Video%20Segments/a_001.ts`;
    const native = createNativeMedia(canonicalUrl);
    const {document, documentHandlers, bodyHandlers} = createBrowserDocument(native.media);
    const videojs = function videojs() {};
    videojs.addLanguage = () => {};
    videojs.Vhs = {xhr: {}};

    const mediaContext = vm.createContext({
        document,
        window: {
            videojs,
            lessonMediaTranslations: {},
            location: {href: 'https://app.example/'},
            setTimeout: () => 1,
            clearTimeout: () => {},
        },
        videojs,
        URL,
        Date,
        gettext: value => value,
        console,
    });
    vm.runInContext(mediaSource, mediaContext);

    assert.equal(typeof documentHandlers['media-session-ready'], 'function');
    assert.equal(typeof documentHandlers['media-recovery-starting'], 'function');
    assert.equal(typeof documentHandlers['media-recovery-failed'], 'function');
    assert.equal(typeof bodyHandlers['htmx:before:swap'], 'function');
    assert.equal(typeof bodyHandlers['htmx:after:swap'], 'function');
    const hook = videojs.Vhs.xhr.beforeRequest;
    assert.equal(typeof hook, 'function', 'the VHS request hook is installed once');
    assert.equal(hook.__lessonMediaGateHook, true);

    assert.equal(
        hook({uri: gateSegmentUrl}).uri,
        gateSegmentUrl,
        'no token is applied before a session token is held',
    );

    documentHandlers['media-session-ready']({
        detail: {element: native.media, url: canonicalUrl, gateToken: OLD_TOKEN},
    });
    assert.equal(native.media._gateToken, OLD_TOKEN);

    const loadsBeforeRefresh = native.loadCount();
    native.media.dispatchEvent({
        type: 'media-gate-refresh',
        detail: {element: native.media, token: NEW_TOKEN, tokenChanged: true},
    });
    assert.equal(native.loadCount(), loadsBeforeRefresh + 1, 'native HLS refetches the manifest');
    assert.match(native.media.src, /_gate_refresh=NewToken01234567$/, 'the native swap carries the new token');
    assert.equal(
        native.media._mediaSourceUrl,
        canonicalUrl,
        'recovery keeps the canonical session URL',
    );

    native.listeners.loadedmetadata.slice().forEach(handler => handler());
    assert.equal(native.media.currentTime, 98, 'native resume seeks two seconds before the prior position');
    assert.equal(native.source.src, canonicalUrl, 'the temporary cache-buster is removed from the source');
    assert.equal(native.media._mediaSourceUrl, canonicalUrl);

    assert.equal(
        hook({uri: gateSegmentUrl}).uri,
        `https://cdn.example/g/${NEW_TOKEN}/course/Video%20Segments/a_001.ts`,
        'a stale manifest token is rewritten on the first request from the media edge',
    );
    assert.equal(
        hook({uri: `/g/${OLD_TOKEN}/course/Video%20Segments/a_001.ts`}).uri,
        `/g/${NEW_TOKEN}/course/Video%20Segments/a_001.ts`,
        'same-origin relative gate URLs are rewritten in place',
    );
    const offOriginUrl = `https://other.example/g/MidToken01234567/course/Video%20Segments/a_001.ts`;
    assert.equal(hook({uri: offOriginUrl}).uri, offOriginUrl, 'an unrelated cross-origin URL is untouched');
    const manifestUrl = `https://cdn.example/g/${OLD_TOKEN}/course/lesson.m3u8`;
    assert.equal(hook({uri: manifestUrl}).uri, manifestUrl, 'manifests are never rewritten');
    const plainUrl = 'https://cdn.example/course/Video%20Segments/a_001.ts';
    assert.equal(hook({uri: plainUrl}).uri, plainUrl, 'non-gated segments are never rewritten');

    assert.equal(native.playCount(), 1, 'a playing player resumes after the refresh');

    const loadsBeforeForeign = native.loadCount();
    native.media.dispatchEvent({
        type: 'media-gate-refresh',
        detail: {element: {tagName: 'AUDIO'}, token: NEXT_TOKEN, tokenChanged: true},
    });
    assert.equal(native.loadCount(), loadsBeforeForeign, 'events for a different media element are ignored');
    assert.equal(
        hook({uri: gateSegmentUrl}).uri,
        `https://cdn.example/g/${NEW_TOKEN}/course/Video%20Segments/a_001.ts`,
        'a foreign event does not rotate the held token',
    );

    native.media.currentTime = 100;
    native.media.paused = true;
    native.media._wasActivelyPlaying = false;
    const loadsBeforePaused = native.loadCount();
    const playsBeforePaused = native.playCount();
    native.media.dispatchEvent({
        type: 'media-gate-refresh',
        detail: {element: native.media, token: NEXT_TOKEN, tokenChanged: true},
    });
    assert.equal(native.loadCount(), loadsBeforePaused + 1, 'a paused player refetches the manifest');
    native.listeners.loadedmetadata.slice().forEach(handler => handler());
    assert.equal(native.media.currentTime, 98);
    assert.equal(native.playCount(), playsBeforePaused, 'a paused player stays paused');

    documentHandlers['media-session-ready']({
        detail: {element: native.media, url: canonicalUrl, gateToken: NEXT_TOKEN},
    });
    assert.equal(videojs.Vhs.xhr.beforeRequest, hook, 'repeated initialization does not stack hooks');

    const loadsBeforeDispose = native.loadCount();
    bodyHandlers['htmx:before:swap']({
        detail: {ctx: {target: {id: 'display-page', querySelectorAll: () => [native.media]}}},
    });
    native.media.dispatchEvent({
        type: 'media-gate-refresh',
        detail: {element: native.media, token: OLD_TOKEN, tokenChanged: true},
    });
    assert.equal(native.loadCount(), loadsBeforeDispose, 'a disposed player ignores a late gate event');
}

Promise.resolve()
    .then(checkTrackerRefreshesGate)
    .then(checkGateHookAndNativeFallback)
    .then(() => console.log('Phase 7 web/PWA player contract checks passed.'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
