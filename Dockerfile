FROM python:3.11-slim

# Python runtime behaviour
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by psycopg2 and other binary libs,
# plus native FFmpeg/FFprobe used only by the dedicated media-worker Celery
# service (server-side FFmpeg migration). The ffmpeg package ships ffprobe.
# FFmpeg never runs from the web startup command or the general Celery worker.
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gcc \
        libpq-dev \
        ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

# Production WSGI entry point. The application Compose services provide the
# production worker/thread/timeout options for both blue/green slots.
CMD ["gunicorn", "learning_platform.wsgi:application", "--bind", "0.0.0.0:8000"]
