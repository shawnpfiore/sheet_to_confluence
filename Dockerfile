FROM python:3.11-slim

WORKDIR /app

# System deps (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

# Copy code
COPY generate_lessons_from_sheet.py /app/generate_lessons_from_sheet.py

# Python deps
RUN pip install --no-cache-dir \
    requests \
    google-api-python-client \
    google-auth \
    google-auth-httplib2

# Helpful for logs
ENV PYTHONUNBUFFERED=1

# Default envs (overridden by k8s if needed)
# need to update using for MCP
ENV OLLAMA_API_URL="https://thehive.tib.ad.ea.com/api/generate" \
    OLLAMA_MODEL="codellama:7b"

CMD ["python", "/app/generate_lessons_from_sheet.py"]
