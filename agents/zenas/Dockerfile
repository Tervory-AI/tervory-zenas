FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
WORKDIR /app
COPY zenas/requirements-room.lock ./requirements.lock
# Chromium for patchright. The binaries sit outside the agent home so the
# unprivileged process can launch them. SAFLII is the only caller.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN --mount=type=secret,id=system_ca \
    if [ -s /run/secrets/system_ca ]; then \
      export PIP_CERT=/run/secrets/system_ca SSL_CERT_FILE=/run/secrets/system_ca \
        REQUESTS_CA_BUNDLE=/run/secrets/system_ca CURL_CA_BUNDLE=/run/secrets/system_ca \
        NODE_EXTRA_CA_CERTS=/run/secrets/system_ca GIT_SSL_CAINFO=/run/secrets/system_ca; \
    fi; \
    printf '%s\n' 'cryptography==50.0.2' > /tmp/zenas-constraints.txt \
    && pip install --no-cache-dir --require-hashes -r requirements.lock -c /tmp/zenas-constraints.txt \
    && python -m patchright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY _chassis/chassis /app/chassis
COPY zenas/app.py /app/app.py
COPY zenas/gold_sources.py /app/agents/zenas/gold_sources.py
COPY zenas/schema.sql /app/agents/zenas/schema.sql
COPY zenas/core /app/agents/zenas/core
COPY zenas/pool /app/agents/zenas/pool
COPY zenas/__init__.py /app/agents/zenas/__init__.py
USER agent
ENV AGENT_NAME=zenas \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
