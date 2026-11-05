FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data \
    && chown agent:agent /data
COPY _chassis/chassis /app/chassis
COPY rig/rigbox /app/rigbox
# Refuse an image whose current page assets were not published as retained objects.
RUN python -c 'import hashlib; from pathlib import Path; root = Path("rigbox/page/v5"); names = ("rig.css", "rig.js"); assert all((root / "assets" / (hashlib.sha256((root / name).read_bytes()).hexdigest() + "-" + name)).read_bytes() == (root / name).read_bytes() for name in names)'
COPY rig/app.py /app/app.py
COPY rig/gold_sources.py /app/gold_sources.py
COPY rig/schema.sql /app/schema.sql
COPY rig/english-20k.txt.gz /app/english-20k.txt.gz
COPY rig/shopping.txt /app/shopping.txt
USER agent
ENV AGENT_NAME=rig \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "rigbox.three_d.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
