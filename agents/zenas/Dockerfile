FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt .
COPY zenas/requirements.txt ./zenas-requirements.txt
# Chromium for patchright. The binaries sit outside the agent home so the
# unprivileged process can launch them. SAFLII is the only caller.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir -r requirements.txt -r zenas-requirements.txt "cryptography==50.0.2" \
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
