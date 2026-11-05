# LiteLLM with Tank's model rosters baked in (no bind mount needed under Coolify).
# config.yaml is the US roster; config.za.yaml is the South Africa roster.
# Pinned to an exact release. LiteLLM retired the rolling main-latest tag in 2026
# (https://docs.litellm.ai/blog/cleaner-release-versions). Upgrade by changing this
# version on purpose in its own PR, never by a moving tag.
# v1.106.4 (2026-10-26): current patch on the 1.106 line. Verify node -v after deploy.
FROM ghcr.io/berriai/litellm:v1.106.4
COPY config.yaml /app/config.yaml
COPY config.za.yaml /app/config.za.yaml
RUN addgroup -S -g 10001 tervory \
    && adduser -S -D -u 10001 -G tervory -h /home/tervory tervory \
    && mkdir -p /home/tervory /var/lib/litellm-chatgpt \
    && chown tervory:tervory /home/tervory /var/lib/litellm-chatgpt \
    && chmod 700 /var/lib/litellm-chatgpt
# Application code and baked-in rosters remain readable and root-owned. Recursively
# chowning /app copies the upstream dependency tree into a new image layer and can
# exceed the production deployment timeout. Runtime state belongs in HOME or /tmp.
ENV HOME=/home/tervory
# Build-only ChatGPT sign-in (docs/ops/CHATGPT-BUILD-MODEL.md). The compose file mounts a named
# volume here so the token survives redeploys; Docker seeds the empty volume with this
# directory's owner (10001), so LiteLLM can write the refreshed token back.
ENV CHATGPT_TOKEN_DIR=/var/lib/litellm-chatgpt
USER 10001:10001
