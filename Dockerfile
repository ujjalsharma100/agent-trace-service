# Minimal image for local dev (docker-compose.dev.yml), CLI CI tests
# (agent-trace-cli/tests/docker-compose.test.yml), and production-style runs.
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# Database schema is created at container start (see docker-compose command).
CMD ["python", "app.py"]
