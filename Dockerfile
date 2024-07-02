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
ENV GUNICORN_CMD_ARGS="--bind=[::]:6060 --workers=2"

# Copy project
COPY . /app/
# Run the application
CMD ["venv/bin/gunicorn", "api.wsgi:app", "--log-file=-", "--log-level", "debug", "--preload", "--workers", "1"]