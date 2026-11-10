FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set working directory
WORKDIR /app

# Install git
RUN apt-get update && apt-get install -y git

# Install dependencies
COPY requirements.txt /app/
RUN python -m venv venv && \
    . venv/bin/activate && \
    pip install --no-cache-dir -r requirements.txt

EXPOSE 6060

# Bind to both IPv4 and IPv6
ENV GUNICORN_CMD_ARGS="--bind=[::]:6060"

# Put venv on PATH so installed CLI tools (e.g. git-fame) are accessible
ENV PATH="/app/venv/bin:$PATH"

# Copy project
COPY . /app/

# Commit sha reported by /api/health. Passed by .github/workflows/main.yml
# (`fly deploy --build-arg COMMIT_SHA=...`); declared after the pip layer so
# a new sha doesn't bust the dependency cache.
ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}
# Run the application: gthread worker class allows concurrent I/O-bound requests
# without head-of-line blocking from a single sync worker.
CMD ["venv/bin/gunicorn", "api.wsgi:app", "--log-file=-", "--log-level", "info", "--preload", "--worker-class", "gthread", "--workers", "2", "--threads", "8", "--timeout", "120"]