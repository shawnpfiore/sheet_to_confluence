FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl bash && \
    rm -rf /var/lib/apt/lists/*

COPY sheet_to_confluence.py /app/sheet_to_confluence.py
COPY entrypoint.sh /app/entrypoint.sh
COPY api_server.py /app/api_server.py

RUN pip install --no-cache-dir \
    requests \
    google-api-python-client \
    google-auth \
    google-auth-httplib2 \
    python-dotenv \
    fastapi \
    uvicorn

RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "9101"]
