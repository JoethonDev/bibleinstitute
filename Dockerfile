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

# Use Django development server for local launch.
# Production WSGI server (e.g. Gunicorn) requires separate approval.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
