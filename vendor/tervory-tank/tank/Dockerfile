# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt constraints-langgraph.txt ./
COPY requirements-routellm.txt requirements-routellm-wheel.txt routellm_build.py semantic_encoder.py ./
# Heavy dependencies and both CPU models install BEFORE the code is copied,
# so an ordinary code change reuses this cached layer instead of reinstalling ~2 GB.
# Optional trust bundle for managed cloud builds; never retained in a layer.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt -r requirements-routellm.txt && \
    pip install --no-cache-dir --no-deps --require-hashes -r requirements-routellm-wheel.txt && \
    python routellm_build.py /opt/routellm && \
    python semantic_encoder.py
# Every Tank module is copied by pattern, so a PR that adds a module never edits this line
# (it used to be a hand-kept list that clashed on almost every Tank PR).
COPY *.py schema.sql cl100k_base.tiktoken cl100k_base.LICENSE ./
COPY command_tools ./command_tools
COPY fonts ./fonts
COPY skills ./skills
COPY training_jobs ./training_jobs
COPY plugin_packages ./plugin_packages
RUN useradd -m tank && chown -R tank /app
# Baked at image build when GIT_SHA (local build) or SOURCE_COMMIT (Coolify with
# "include source commit in build" on) is passed. Otherwise tank_sha() reads
# SOURCE_COMMIT from the runtime environment.
ARG SOURCE_COMMIT=
ARG GIT_SHA=
ENV LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
ENV TANK_GIT_SHA=${GIT_SHA:-$SOURCE_COMMIT}
USER tank
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
