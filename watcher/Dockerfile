# Cloud Run Job image for the background price watcher (surrounding awareness).
# Self-contained: build context is THIS folder only — no agent code needed.
FROM python:3.12-slim

WORKDIR /app

COPY requirements-watcher.txt .
RUN pip install --no-cache-dir -r requirements-watcher.txt

COPY memory_client.py watcher_job.py ./

# Cloud Run Jobs run this once per execution, then exit.
CMD ["python", "watcher_job.py"]
