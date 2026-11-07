"""Audit repository text for the ASCII + Arabic character policy.

Repository policy: first-party text files contain ASCII characters and Arabic
script only. Vendored libraries, minified assets, compiled catalogs and scratch
directories are exempt because they are third-party or generated artefacts.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

ARABIC_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)

TEXT_SUFFIXES = {
    ".py", ".html", ".htm", ".js", ".mjs", ".css", ".yml", ".yaml", ".md", ".po",
    ".pot", ".sh", ".ps1", ".conf", ".json", ".txt", ".csv", ".env", ".cfg",
    ".ini", ".toml", ".xml", ".svg", ".example",
}
EXEMPT_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages",
    "collectstatic", "media", "runtime", "vendor", "videojs", "pdfjs", "hls",
    "tmp",
}
EXEMPT_NAMES = {
    "Dockerfile", ".env.example", "manage.py", ".dockerignore",
    ".gitignore", ".gitattributes", ".editorconfig",
}


def _is_arabic(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in ARABIC_RANGES)


def _is_allowed(char: str) -> bool:
    return char in "\n\r\t" or 32 <= ord(char) < 127 or _is_arabic(char)


class Command(BaseCommand):
    help = "Report non-ASCII/non-Arabic characters in repository text files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(Path(__file__).resolve().parents[3]),
            help="Root directory to scan (defaults to the repository root).",
        )
        parser.add_argument(
            "--max-reports",
            type=int,
            default=200,
            help="Stop after this many reported lines (default 200).",
        )

    def handle(self, *args, **options):
        root = Path(options["path"]).resolve()
        max_reports = max(1, int(options["max_reports"]))
        if not root.is_dir():
            raise CommandError(f"Not a directory: {root}")

        findings: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name not in EXEMPT_DIRS)
            for name in sorted(filenames):
                suffix = os.path.splitext(name)[1].lower()
                if suffix not in TEXT_SUFFIXES and name not in EXEMPT_NAMES:
                    continue
                if name.endswith(".min.js") or name.endswith(".min.css"):
                    continue
                path = Path(dirpath) / name
                scanned += 1
                try:
                    with path.open("r", encoding="utf-8", errors="strict") as handle:
                        for line_number, line in enumerate(handle, 1):
                            bad = [char for char in line if not _is_allowed(char)]
                            if not bad:
                                continue
                            positions = ", ".join(
                                f"U+{ord(char):04X} {unicodedata.name(char, 'UNKNOWN')}"
                                for char in sorted(set(bad))
                            )
                            findings.append(f"{path.relative_to(root)}:{line_number}: {positions}")
                            if len(findings) >= max_reports:
                                break
                except (UnicodeDecodeError, OSError) as exc:
                    findings.append(f"{path.relative_to(root)}: unreadable ({exc})")
                if len(findings) >= max_reports:
                    break
            if len(findings) >= max_reports:
                break

        if findings:
            for line in findings:
                self.stderr.write(line)
            raise CommandError(
                f"{len(findings)} line(s) contain characters outside ASCII and Arabic; "
                "replace them with ASCII text."
            )
        self.stdout.write(
            self.style.SUCCESS(f"Encoding audit passed: {scanned} text file(s) contain only ASCII and Arabic.")
        )
