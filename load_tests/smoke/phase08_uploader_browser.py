#!/usr/bin/env python3
"""Browser check for the direct uploader on the signup page.

Needs Playwright with Chromium installed. Starts Django against the disposable
database, stubs every provider call at the browser boundary (authorize, PUT,
discard) so no real storage or network side effect happens, and verifies: files
upload on pick in the background, the capability survives a reload, and Remove
clears both the stored token and the hidden input while calling the discard
endpoint.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")

sys.path.insert(0, str(ROOT))
import django

django.setup()

from django.urls import reverse

GRANT = {
    "token": "browser-token-1",
    "upload_url": "https://upload.invalid/put",
    "key": "direct-upload-staging/x.png",
    "expires_at": "2030-01-01T00:00:00+00:00",
    "max_bytes": 10485760,
    "content_types": ["image/png"],
    "extensions": [".png"],
    "audio_seconds": None,
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"server did not answer at {url}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    port = free_port()
    env = dict(os.environ)
    env.update(
        {
            "DJANGO_DEBUG": "1",
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    checks: list[str] = []
    try:
        base = f"http://127.0.0.1:{port}"
        signup_path = reverse("signup")
        wait_for(base + "/login/")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            discard_calls: list[str] = []
            authorize_calls: list[str] = []

            def handle_authorize(route):
                authorize_calls.append(route.request.url)
                route.fulfill(status=200, content_type="application/json", body=json.dumps(GRANT))

            def handle_discard(route):
                discard_calls.append(route.request.url)
                route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

            page.route("**/api/direct-upload/authorize/**", handle_authorize)
            page.route("**/api/direct-upload/discard/**", handle_discard)
            page.route("https://upload.invalid/**", lambda route: route.fulfill(status=200, body=""))

            page.goto(base + signup_path, wait_until="domcontentloaded")
            picker = page.query_selector("input[type=file][data-upload-purpose]")
            if picker is None:
                print("FAILED: the signup page has no annotated file picker")
                return 1
            checks.append("signup page exposes an annotated picker")

            picker.set_input_files(
                files=[{"name": "id.png", "mimeType": "image/png", "buffer": b"\x89PNG\r\n\x1a\n" + b"0" * 32}]
            )
            page.wait_for_function(
                "() => !!document.querySelector('input[name=\"direct_upload_token_identity_front\"]')",
                timeout=15000,
            )
            if not authorize_calls:
                failures.append("picking a file did not call the authorize endpoint")
            else:
                checks.append("picking a file uploads in the background")
            status_text = page.eval_on_selector(".direct-upload-status", "el => el.textContent")
            if status_text.strip() == "":
                failures.append("the picker did not report an uploaded state")
            else:
                checks.append("picker shows the uploaded state")

            page.reload(wait_until="domcontentloaded")
            restored = page.evaluate(
                "() => document.querySelectorAll('input[name=\"direct_upload_token_identity_front\"]').length"
            )
            if restored != 1:
                failures.append(f"reload restored {restored} hidden tokens instead of 1")
            else:
                checks.append("reload restores the uploaded capability")
            remove_visible = page.evaluate(
                "() => {const b = document.querySelector('.direct-upload-remove'); return !!(b && !b.hidden);}"
            )
            if not remove_visible:
                failures.append("the Remove control is not visible after reload")
            else:
                checks.append("Remove control available after reload")

            page.click(".direct-upload-remove")
            page.wait_for_function(
                "() => document.querySelectorAll('input[name=\"direct_upload_token_identity_front\"]').length === 0",
                timeout=10000,
            )
            if not discard_calls:
                failures.append("Remove did not call the discard endpoint")
            else:
                checks.append("Remove discards the capability")
            stored = page.evaluate(
                "() => Object.keys(window.sessionStorage).filter(k => k.indexOf('directUpload:') === 0).length"
            )
            if stored:
                failures.append("Remove left the stored capability behind")
            else:
                checks.append("Remove clears the stored capability")

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    print("PASS: " + ", ".join(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
