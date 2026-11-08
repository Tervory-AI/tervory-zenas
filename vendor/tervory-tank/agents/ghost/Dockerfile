FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt .
COPY ghost/requirements.txt ghost-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r ghost-requirements.txt \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data && chown agent:agent /data
# Watch startup imports the shared tenant-memory client.
COPY _chassis/chassis /app/chassis
# Same unmodified SMTP sender Tiger uses; no Rig product code is invoked.
COPY rig/rigbox/reserve.py /app/agents/rig/rigbox/reserve.py
# Shared API client only; Autumbra never invokes Tisan product logic.
COPY tisan/promotion.py /app/enforce_coolify.py
COPY ghost /app/ghost
COPY rig/docker-compose.room.yml /app/ghost/docker-compose.room.yml
USER agent
ENV AGENT_NAME=ghost DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "ghost.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
