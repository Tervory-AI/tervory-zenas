# Tiger: Tervory's browser. A real Chromium that loads a page the way a person's browser does,
# runs its JavaScript and waits for it to settle. Internal only; Tank's skills call it.
FROM mcr.microsoft.com/playwright/python:v1.64.1-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Playwright 1.64.1's driver bundles Node 24.22.1. This repo stays on Node 22.
# Install Node 22.23.4 (exact patch, SHA256 from https://nodejs.org/dist/v22.23.4/SHASUMS256.txt;
# the build fails on a mismatch) and point Playwright at it with PLAYWRIGHT_NODEJS_PATH.
# The bundled binary is replaced by a link to the same node, so the container runs one Node.
ARG NODE_VERSION=22.23.4
RUN arch="$(uname -m)" \
    && case "$arch" in \
         x86_64) node_arch=x64; node_sha256=b8492284c76a54baf58d61668dd0e41ee0fc98adabf9503d30f05d08f55b4406 ;; \
         aarch64) node_arch=arm64; node_sha256=f7a1e486dbc8b286847815b477d4fb5e9fb963ed4489c3e1588853d685f589f9 ;; \
         *) echo "unsupported architecture $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.gz" -o /tmp/node.tar.gz \
    && echo "${node_sha256}  /tmp/node.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/node.tar.gz -C /usr/local/bin --strip-components=2 "node-v${NODE_VERSION}-linux-${node_arch}/bin/node" \
    && rm /tmp/node.tar.gz \
    && driver="$(python -c 'import os, playwright; print(os.path.join(os.path.dirname(playwright.__file__), "driver"))')" \
    && ln -sf /usr/local/bin/node "$driver/node" \
    && /usr/local/bin/node -v | grep -qx "v${NODE_VERSION}" \
    && "$driver/node" -v | grep -qx "v${NODE_VERSION}"
ENV PLAYWRIGHT_NODEJS_PATH=/usr/local/bin/node
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY app.py pool.py ocr.py ./
USER pwuser
EXPOSE 3000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
