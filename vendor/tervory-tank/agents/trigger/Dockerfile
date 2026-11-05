FROM python:3.12-slim
WORKDIR /app
COPY _chassis/requirements.txt /app/chassis-requirements.txt
COPY trigger/requirements.txt /app/trigger-requirements.txt
RUN pip install --no-cache-dir -r chassis-requirements.txt -r trigger-requirements.txt \
    && useradd --create-home --uid 1000 agent
COPY _chassis/chassis /app/chassis
COPY trigger /app/trigger
# Reuse Zenas's civil/criminal scope rules unchanged, never a second legal engine.
COPY zenas/core/scope_rules.py /app/zenas_scope.py
COPY zenas/core/areas.json /app/areas.json
RUN chmod -R a+rX /app
USER agent
ENV AGENT_NAME=trigger PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "trigger.app:app", "--host", "0.0.0.0", "--port", "8000"]
