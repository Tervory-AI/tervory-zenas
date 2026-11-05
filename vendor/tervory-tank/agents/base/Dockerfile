FROM python:3.12-slim
WORKDIR /app
COPY agents/_chassis/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data /browser-evidence \
    && chown agent:agent /data /browser-evidence
COPY agents/_chassis/chassis /app/chassis
COPY agents/base/app.py /app/app.py
COPY agents/base/pipeline.py /app/pipeline.py
COPY tank/base_source_scores.py /app/base_source_scores.py
COPY agents/base/room.py /app/room.py
COPY agents/base/base_brain.py /app/base_brain.py
COPY agents/base/browser_evidence.py /app/browser_evidence.py
COPY agents/base/access_sources.py /app/access_sources.py
COPY agents/base/sources /app/sources
# Engine data files only (word lists, tables). Skill code stays in Skala.
# Every version's data is in the image; at runtime Base reads only the approved version's folder.
COPY tank/skills /tmp/skills
RUN mkdir -p /app/skills && cd /tmp/skills \
    && find . -path './*.profile/*/data/*.json' -exec cp --parents {} /app/skills/ \; \
    && rm -rf /tmp/skills
COPY agents/base/base_gold /app/base_gold
COPY agents/base/engines /app/engines
# Reuse the existing public-address validator; no second URL policy.
COPY agents/_brain/security.py /app/reference_security.py
USER agent
ENV AGENT_NAME=base \
    DATA_DIR=/data \
    SKILLS_DIR=/app/skills \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
