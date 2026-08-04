FROM python:3.11-slim

# Python runtime behaviour
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by psycopg2 and other binary libs
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

EXPOSE 8000

# Production WSGI entry point. The application Compose services provide the
# production worker/thread/timeout options for both blue/green slots.
CMD ["gunicorn", "learning_platform.wsgi:application", "--bind", "0.0.0.0:8000"]
