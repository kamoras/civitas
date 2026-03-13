FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ libxml2-dev libxslt-dev curl gosu && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd -r civitas && useradd -r -g civitas -d /app civitas \
    && mkdir -p /data /app/.cache && chown -R civitas:civitas /data /app/.cache

EXPOSE 8000

COPY <<'ENTRY' /entrypoint.sh
#!/bin/sh
chown -R civitas:civitas /data /app/.cache
exec gosu civitas "$@"
ENTRY
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
