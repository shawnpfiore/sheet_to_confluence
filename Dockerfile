FROM python:3.11-slim

# System deps (optional but common for google client)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY sheet_to_confluence.py /app/sheet_to_confluence.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Non-root
RUN useradd -m appuser
USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]
