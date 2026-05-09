# Minimal image for local / CI integration tests (see agent-trace-cli/tests/docker-compose.test.yml).
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
