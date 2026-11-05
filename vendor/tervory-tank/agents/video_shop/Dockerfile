# Build with agents/ as context. This room composes the existing Rig host.
FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
COPY video_shop/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data && chown agent:agent /data
COPY _chassis/chassis /app/chassis
COPY rig/rigbox /app/rigbox
COPY rig/app.py /app/app.py
COPY rig/gold_sources.py /app/gold_sources.py
COPY rig/schema.sql /app/schema.sql
COPY rig/english-20k.txt /app/english-20k.txt
COPY rig/shopping.txt /app/shopping.txt
COPY video_shop /app/video_shop
RUN python /app/video_shop/prepare_models.py /app/video_shop/models
USER agent
# Rig owns shared catalogue/cart schema and existing Tank identity.
ENV AGENT_NAME=rig DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONPATH=/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
EXPOSE 8000
CMD ["uvicorn", "video_shop.video_asgi:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
