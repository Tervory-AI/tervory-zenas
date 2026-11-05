# Stage 1, build time only: the real OSV advisory data for PyPI and npm, from the
# official public OSV bucket. ADD re-checks the remote file on every build, so a
# redeploy picks up new advisories. osv_bake.py opens no socket; it turns the two
# zips into one indexed /out/osv.sqlite (~35 MB). The zips stay in this stage.
# Radix itself never downloads anything at runtime: the room has no internet route.
FROM python:3.12-slim AS osv
ARG OSV_BUCKET=https://osv-vulnerabilities.storage.googleapis.com
ADD ${OSV_BUCKET}/PyPI/all.zip /osv/PyPI.zip
ADD ${OSV_BUCKET}/npm/all.zip /osv/npm.zip
COPY radix/scanlib /bake/scanlib
COPY radix/osv_bake.py /bake/osv_bake.py
RUN mkdir -p /out \
    && python /bake/osv_bake.py /out/osv.sqlite PyPI=/osv/PyPI.zip npm=/osv/npm.zip

# R6 signatures are downloaded only while building this stage.
FROM python:3.12-slim AS signatures
RUN apt-get update && apt-get install -y --no-install-recommends clamav-freshclam ca-certificates \
    && mkdir -p /out && chown clamav:clamav /out \
    && freshclam --datadir=/out --stdout

FROM python:3.12-slim

# Pinned scanners. The downloads run at image build. A scan does not use the network.
# Gitleaks 8.30.1 checksums are from the upstream checksums file for that tag.
# Semgrep and pip-audit live in their own venvs so they cannot change the chassis packages.
# pip-audit and npm audit call a live service, so the scan reads the OSV files in
# /opt/osv instead of executing those clients.
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA_X64=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ARG GITLEAKS_SHA_ARM64=e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080
ARG SEMGREP_VERSION=1.182.1
ARG PIP_AUDIT_VERSION=2.10.1
# Checkov is Apache-2.0; isolated from Semgrep and chassis dependencies.
ARG CHECKOV_VERSION=3.3.21
ARG TARGETARCH

WORKDIR /app
COPY _chassis/requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tar unzip default-jre-headless clamav nodejs npm bash openssl \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}" \
    && python -m venv /opt/audit \
    && /opt/audit/bin/pip install --no-cache-dir "pip-audit==${PIP_AUDIT_VERSION}" \
    && arch="${TARGETARCH:-amd64}" \
    && case "$arch" in \
         amd64) gl_arch=x64; gl_sha="${GITLEAKS_SHA_X64}" ;; \
         arm64) gl_arch=arm64; gl_sha="${GITLEAKS_SHA_ARM64}" ;; \
         *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${gl_arch}.tar.gz" \
    && echo "${gl_sha}  /tmp/gitleaks.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && chmod 755 /usr/local/bin/gitleaks \
    && rm -rf /tmp/gitleaks.tar.gz /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data /opt/osv \
    && chown agent:agent /data /opt/osv
RUN python -m venv /opt/checkov \
    && /opt/checkov/bin/pip install --no-cache-dir "checkov==${CHECKOV_VERSION}"
COPY radix/install-passive-tools.sh /tmp/install-passive-tools.sh
RUN sh /tmp/install-passive-tools.sh \
    && ln -s /opt/bin/nuclei /usr/local/bin/nuclei \
    && pip install --no-cache-dir dnspython==2.9.0 PyYAML==6.0.3 \
    && rm /tmp/install-passive-tools.sh
COPY radix/install-infra-tools.sh /tmp/install-infra-tools.sh
RUN sh /tmp/install-infra-tools.sh && rm /tmp/install-infra-tools.sh
COPY radix/install-watch-tools.sh /tmp/install-watch-tools.sh
RUN sh /tmp/install-watch-tools.sh && rm /tmp/install-watch-tools.sh
COPY radix/browser/package*.json /app/browser/
RUN cd /app/browser && npm ci --ignore-scripts --no-audit --no-fund \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/radix-browsers npx playwright install --with-deps chromium
COPY radix/browser/check.cjs /app/browser/check.cjs
COPY --from=signatures /out/ /var/lib/clamav/
COPY _chassis/chassis /app/chassis
COPY radix/app.py /app/app.py
COPY radix/gold_sources.py /app/gold_sources.py
COPY radix/evals/gold /app/evals/gold
COPY radix/net.py /app/net.py
COPY radix/live.py /app/live.py
COPY radix/egress.py /app/egress.py
COPY radix/egress-policy.json /app/egress-policy.json
COPY radix/passive_*.py radix/regulation_events.py radix/active_*.py /app/
COPY radix/verify /app/verify
COPY radix/schema.sql /app/schema.sql
COPY radix/scanlib /app/scanlib
COPY radix/scanlib/osv/ /opt/osv/
RUN chown -R agent:agent /app /opt/osv
# Root-owned and read-only for the agent; copied after the chown so it is one layer.
COPY --from=osv /out/osv.sqlite /opt/osv/osv.sqlite
# Policy is a merged source file, not a writable runtime setting.
COPY radix/infra-ports.json radix/active-domains.json radix/active_guard.js /app/
RUN chmod 0444 /app/infra-ports.json /app/active-domains.json /app/active_guard.js
# testssl requires hexdump even with DNS disabled. No runtime package install.
RUN apt-get update && apt-get install -y --no-install-recommends bsdextrautils \
    && rm -rf /var/lib/apt/lists/*
USER agent
ENV AGENT_NAME=radix \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    RADIX_USE_SYSTEM_TOOLS=1 \
    RADIX_OSV_DIR=/opt/osv \
    SEMGREP_BIN=/opt/semgrep/bin/semgrep \
    CHECKOV_BIN=/opt/checkov/bin/checkov \
    PIP_AUDIT_BIN=/opt/audit/bin/pip-audit \
    SEMGREP_SEND_METRICS=off \
    SEMGREP_ENABLE_VERSION_CHECK=0 \
    GIT_TERMINAL_PROMPT=0
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
