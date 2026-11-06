# Pool loader only. The Zenas room image does not install these packages.
# Build context is agents/ (the parent of zenas/).
FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt /tmp/chassis.txt
COPY zenas/requirements.txt /tmp/zenas.txt
# Chromium for patchright. Same install as the Zenas room: the binaries sit
# outside the agent home so the unprivileged process can launch them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir -r /tmp/chassis.txt -r /tmp/zenas.txt \
    && python -c "from huggingface_hub import HfApi, __version__; assert __version__ == '1.7.2'; assert callable(HfApi.list_jobs_hardware); assert callable(HfApi.run_uv_job)" \
    && python -m patchright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY _chassis/chassis /app/chassis
COPY zenas /app/agents/zenas
COPY _chassis/chassis /app/chassis
COPY tiger/alert_email.py /app/agents/tiger/alert_email.py
COPY rig/rigbox/reserve.py /app/agents/rig/rigbox/reserve.py
USER agent
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
ENTRYPOINT ["nice", "-n", "19", "python", "-m", "agents.zenas.pool.bulk"]
