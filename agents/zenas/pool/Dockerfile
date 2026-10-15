# Pool loader only. The Zenas room image does not install these packages.
# Build context is agents/ (the parent of zenas/).
FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt /tmp/chassis.txt
COPY zenas/requirements.txt /tmp/zenas.txt
RUN pip install --no-cache-dir -r /tmp/chassis.txt -r /tmp/zenas.txt \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY zenas /app/agents/zenas
USER agent
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
ENTRYPOINT ["nice", "-n", "19", "python", "-m", "agents.zenas.pool.bulk"]
