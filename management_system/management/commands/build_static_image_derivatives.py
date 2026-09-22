from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - deployment dependency guard
    Image = None
    _PIL_IMPORT_ERROR = exc


DERIVATIVES = (
    ("bible-slider1.png", "bible-slider1.webp", 82),
    ("bible-slider2.png", "bible-slider2.webp", 82),
    ("bible-slider3.png", "bible-slider3.webp", 82),
    ("statement.jpg", "statement.webp", 85),
)


class Command(BaseCommand):
    help = "Build WebP derivatives while preserving the original static images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report missing or stale derivatives without writing files.",
        )

    def handle(self, *args, **options):
        if Image is None:
            raise CommandError(f"Pillow is required to build image derivatives: {_PIL_IMPORT_ERROR}")

        static_root = Path(__file__).resolve().parents[2] / "static"
        check_only = options["check"]
        changed = 0

        for source_name, derivative_name, quality in DERIVATIVES:
            source = static_root / source_name
            derivative = static_root / derivative_name
            if not source.is_file():
                raise CommandError(f"Static source image is missing: {source_name}")

            if (
                check_only
                and derivative.is_file()
                and derivative.stat().st_mtime >= source.stat().st_mtime
            ):
                self.stdout.write(f"OK {derivative_name}")
                continue

            if check_only:
                self.stdout.write(f"MISSING_OR_STALE {derivative_name}")
                changed += 1
                continue

            with Image.open(source) as image:
                image.load()
                image.save(
                    derivative,
                    format="WEBP",
                    quality=quality,
                    method=6,
                )
            changed += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"WROTE {derivative_name} ({derivative.stat().st_size} bytes)"
                )
            )

        if check_only and changed:
            raise CommandError(f"{changed} image derivative(s) missing or stale")
        if check_only:
            self.stdout.write("All static image derivatives are current.")
