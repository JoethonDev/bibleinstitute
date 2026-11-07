FROM python:3.11-slim AS base

# Python runtime behaviour
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies required by psycopg2 and other binary libs. Native
# FFmpeg/FFprobe are NOT installed here: only the dedicated media-worker
# target adds them, so web/general/admin images stay lean.
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gcc \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Generate reproducible WebP derivatives beside their preserved originals.
# Pillow is already provided by the pinned qrcode[pil] dependency.
RUN DJANGO_SECRET_KEY=build-only-static-derivatives python manage.py build_static_image_derivatives

EXPOSE 8000

# Production WSGI entry point. The application Compose services provide the
# production worker/thread/timeout options for both blue/green slots.
CMD ["gunicorn", "learning_platform.wsgi:application", "--bind", "0.0.0.0:8000"]

# Media worker target: adds FFmpeg/FFprobe only here. `ffmpeg` ships ffprobe
# and is used only by the dedicated `media` Celery queue.
FROM base AS media
RUN apt-get update && \
    apt-get install --no-install-recommends -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Default final stage (web, default/general workers, admin jobs).
FROM base AS runtime
