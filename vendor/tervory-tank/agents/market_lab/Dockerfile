FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt /tmp/chassis-requirements.txt
COPY market_lab/feeds/requirements.txt /tmp/feeds-requirements.txt
RUN pip install --no-cache-dir -r /tmp/chassis-requirements.txt -r /tmp/feeds-requirements.txt \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY _chassis/chassis /app/chassis
COPY market_lab/feeds /app/feeds
COPY market_lab/core /app/core
COPY market_lab/engines /app/engines
COPY market_lab/schema.sql /app/schema.sql
# Reuse the same SMTP implementation and uncertain-send guard as Tiger/Zenas.
COPY radix/scanlib/dependencies.py /app/agents/radix/scanlib/dependencies.py
COPY tiger/alert_email.py /app/agents/tiger/alert_email.py
COPY rig/rigbox/reserve.py /app/agents/rig/rigbox/reserve.py
COPY market_lab/app.py /app/app.py
COPY market_lab/gold_sources.py /app/gold_sources.py
USER agent
ENV AGENT_NAME=market_lab \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
