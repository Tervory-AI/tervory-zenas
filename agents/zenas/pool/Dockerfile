# Pool loader only. The Zenas room image does not install these packages.
# Build context is agents/ (the parent of zenas/).
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
WORKDIR /app
COPY _chassis/requirements.lock /tmp/chassis.lock
COPY zenas/requirements.lock /tmp/zenas.lock
# Chromium for patchright. Same install as the Zenas room: the binaries sit
# outside the agent home so the unprivileged process can launch them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN --mount=type=secret,id=system_ca \
    if [ -s /run/secrets/system_ca ]; then \
      export PIP_CERT=/run/secrets/system_ca SSL_CERT_FILE=/run/secrets/system_ca \
        REQUESTS_CA_BUNDLE=/run/secrets/system_ca CURL_CA_BUNDLE=/run/secrets/system_ca \
        NODE_EXTRA_CA_CERTS=/run/secrets/system_ca GIT_SSL_CAINFO=/run/secrets/system_ca; \
    fi; \
    pip install --require-hashes --no-cache-dir -r /tmp/chassis.lock -r /tmp/zenas.lock \
    && python -m patchright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY zenas /app/agents/zenas
USER agent
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
ENTRYPOINT ["nice", "-n", "19", "python", "-m", "agents.zenas.pool.bulk"]
