/**
 * LMS media worker.
 *
 * HLS manifests are signed by Django per segment. This worker verifies the
 * signature, serves only TS objects from the canonical R2 segment folders,
 * and forwards an idempotent receipt to Django after a successful read.
 *
 * Required bindings/secrets:
 *   MY_BUCKET              R2 bucket binding
 *   HMAC_SECRET            same value as Django WORKER_HMAC_SECRET
 *   DJANGO_RECEIPT_URL     absolute Django receipt endpoint
 *   DJANGO_RECEIPT_SECRET  same value as Django WORKER_RECEIPT_SECRET
 *   ALLOWED_DOMAIN         optional comma-separated hostnames; defaults to
 *                          bibleinstitute-eg.org
 */

const encoder = new TextEncoder();
let cachedHmacSecret = null;
let cachedHmacKeyPromise = null;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    const browserOrigin = allowedBrowserOrigin(request, env);
    if (request.method === "OPTIONS") {
      return browserOrigin !== null
        ? corsResponse(null, browserOrigin)
        : new Response("Forbidden", { status: 403 });
    }

    if (request.method === "GET" || request.method === "HEAD") {
      if (browserOrigin === null) return new Response("Forbidden", { status: 403 });

      if (path.startsWith("/media/")) {
        return handleMedia(request, env, ctx, url, browserOrigin);
      }
      if (path.startsWith("/public/")) {
        return servePublic(env, url, browserOrigin);
      }
    }

    return new Response("Not found", { status: 404 });
  },
};

function allowedBrowserOrigin(request, env) {
  const configuredDomains = Array.isArray(env.ALLOWED_DOMAIN)
    ? env.ALLOWED_DOMAIN
    : String(env.ALLOWED_DOMAIN || "bibleinstitute-eg.org").split(",");
  const domains = configuredDomains.map(normalizeAllowedDomain).filter(Boolean);
  const origin = request.headers.get("Origin");
  const referer = request.headers.get("Referer");

  if (origin) {
    try {
      const parsed = new URL(origin);
      return domains.some((domain) => isAllowedHostname(parsed.hostname, domain))
        ? origin
        : null;
    } catch (_error) {
      return null;
    }
  }

  if (referer) {
    try {
      const parsed = new URL(referer);
      return domains.some((domain) => isAllowedHostname(parsed.hostname, domain))
        ? ""
        : null;
    } catch (_error) {
      return null;
    }
  }

  return null;
}

function normalizeAllowedDomain(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const candidate = value.trim();
  try {
    const parsed = new URL(candidate.includes("://") ? candidate : `https://${candidate}`);
    if (parsed.pathname !== "/" || parsed.search || parsed.hash) return null;
    return parsed.hostname.toLowerCase().replace(/^\.+|\.+$/g, "");
  } catch (_error) {
    return null;
  }
}

function isAllowedHostname(hostname, domain) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  return host === domain || host.endsWith(`.${domain}`);
}

async function handleMedia(request, env, ctx, url, browserOrigin) {
  const parts = url.pathname.split("/");
  const sessionId = parts[2];
  const encodedKey = parts.slice(3).join("/");
  if (!sessionId || !encodedKey) return new Response("Bad request", { status: 400 });

  let segmentKey;
  try {
    segmentKey = decodeURIComponent(encodedKey);
  } catch (_error) {
    return new Response("Bad request", { status: 400 });
  }

  if (!isCanonicalSegmentKey(segmentKey)) {
    return new Response("Forbidden", { status: 403 });
  }

  const token = url.searchParams.get("token");
  const tokenVerification = await verifyMediaToken(token, sessionId, segmentKey, env.HMAC_SECRET);
  if (!tokenVerification.valid) {
    return corsResponse("Unauthorized", browserOrigin, 401);
  }
  const segmentNumber = segmentNumberFromKey(segmentKey);
  if (segmentNumber === null) return corsResponse("Forbidden", browserOrigin, 403);

  const cacheRequest = new Request(
    `${url.origin}/__segment-cache/${encodeURIComponent(segmentKey)}`,
    { method: "GET" },
  );
  let cached;
  try {
    cached = await caches.default.match(cacheRequest);
  } catch (_error) {
    cached = undefined;
  }
  if (cached) {
    ctx.waitUntil(recordReceipt(
      env,
      sessionId,
      segmentKey,
      segmentNumber,
      tokenVerification.signature,
    ));
    return browserResponse(cached, browserOrigin, request.method);
  }

  const object = await env.MY_BUCKET.get(segmentKey);
  if (!object) return corsResponse("Not found", browserOrigin, 404);

  const cacheHeaders = new Headers();
  cacheHeaders.set("Content-Type", object.httpMetadata?.contentType || "video/mp2t");
  cacheHeaders.set("Cache-Control", "public, max-age=3600, immutable");
  cacheHeaders.set("Accept-Ranges", "bytes");
  if (object.httpEtag) cacheHeaders.set("ETag", object.httpEtag);

  const cacheableResponse = new Response(object.body, {
    status: 200,
    headers: cacheHeaders,
  });
  ctx.waitUntil(cacheSegment(cacheRequest, cacheableResponse.clone()));
  ctx.waitUntil(recordReceipt(
    env,
    sessionId,
    segmentKey,
    segmentNumber,
    tokenVerification.signature,
  ));
  return browserResponse(cacheableResponse, browserOrigin, request.method);
}

async function cacheSegment(cacheRequest, response) {
  try {
    await caches.default.put(cacheRequest, response);
  } catch (_error) {
    // Cache availability must not affect media delivery.
  }
}

function isCanonicalSegmentKey(key) {
  if (!key || key.startsWith("/") || key.includes("\\") || key.includes("//")) return false;
  const parts = key.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) return false;
  if (parts.length < 2) return false;
  if (!["Video Segments", "Audio Segments"].includes(parts[parts.length - 2])) return false;
  return /\.ts$/i.test(parts[parts.length - 1]);
}

function segmentNumberFromKey(key) {
  const match = key.split("/").pop().match(/(?:^|[_-])(\d+)\.ts$/i);
  return match ? Number.parseInt(match[1], 10) : null;
}

async function verifyMediaToken(token, sessionId, segmentKey, secret) {
  if (!token || !secret) return { valid: false };
  const parts = token.split(":");
  if (parts.length !== 3 || parts[0] !== sessionId) return { valid: false };

  const expiresAt = Number.parseInt(parts[1], 10);
  if (!Number.isInteger(expiresAt) || Math.floor(Date.now() / 1000) >= expiresAt) {
    return { valid: false };
  }

  const expected = await hmacHex(secret, `${sessionId}:${expiresAt}:${segmentKey}`);
  return {
    valid: timingSafeHexEqual(parts[2], expected),
    signature: parts[2],
  };
}

async function recordReceipt(env, sessionId, segmentKey, segmentNumber, signature) {
  const receiptUrl = env.DJANGO_RECEIPT_URL;
  if (!receiptUrl || !env.DJANGO_RECEIPT_SECRET) return;
  try {
    await fetch(receiptUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Worker-Secret": env.DJANGO_RECEIPT_SECRET,
      },
      body: JSON.stringify({
        session_id: sessionId,
        segment_key: segmentKey,
        segment_number: segmentNumber,
        signature,
      }),
    });
  } catch (_error) {
    // Media delivery must not fail because receipt persistence is temporarily
    // unavailable. The request remains idempotent when retried by playback.
  }
}

async function servePublic(env, url, browserOrigin) {
  const key = url.pathname.slice(1);
  if (!key || key.includes("..") || key.includes("\\")) {
    return corsResponse("Forbidden", browserOrigin, 403);
  }
  const object = await env.MY_BUCKET.get(key);
  if (!object) return corsResponse("Not found", browserOrigin, 404);

  const headers = new Headers();
  headers.set("Content-Type", object.httpMetadata?.contentType || "application/octet-stream");
  headers.set("Cache-Control", "public, max-age=86400");
  addCorsHeaders(headers, browserOrigin);
  return new Response(object.body, { headers });
}

async function hmacHex(secret, message) {
  if (cachedHmacSecret !== secret || !cachedHmacKeyPromise) {
    cachedHmacSecret = secret;
    cachedHmacKeyPromise = crypto.subtle.importKey(
      "raw",
      encoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
  }
  const key = await cachedHmacKeyPromise;
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function browserResponse(response, origin, method = "GET") {
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "private, no-store");
  addCorsHeaders(headers, origin);
  return new Response(method === "HEAD" ? null : response.body, {
    status: response.status,
    headers,
  });
}

function timingSafeHexEqual(actual, expected) {
  if (typeof actual !== "string" || actual.length !== expected.length) return false;
  let difference = 0;
  for (let index = 0; index < expected.length; index += 1) {
    difference |= actual.toLowerCase().charCodeAt(index) ^ expected.charCodeAt(index);
  }
  return difference === 0;
}

function addCorsHeaders(headers, origin) {
  if (!origin) return;
  headers.set("Access-Control-Allow-Origin", origin);
  headers.set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Range, Content-Type");
  headers.set("Access-Control-Expose-Headers", "Content-Length, Content-Range, ETag");
  headers.set("Vary", "Origin");
}

function corsResponse(body, origin, status = 200) {
  const headers = new Headers({ "Content-Type": "text/plain; charset=utf-8" });
  addCorsHeaders(headers, origin);
  return new Response(body, { status, headers });
}
