# Token & Tally office server. Standard library only, so the image is just Python plus the app.
# Works on x86-64 PCs and ARM boards such as a Raspberry Pi (64-bit OS).
#   docker compose up -d        (see docs/docker.md)
# Multi-platform base index; update deliberately and rerun both native smoke jobs.
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

LABEL org.opencontainers.image.title="Token & Tally office" \
      org.opencontainers.image.description="A 3D 1990s office where every Claude Code agent is an office worker" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# An unprivileged user; /data (the key and names) is the only place it can write.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin office \
    && mkdir -p /data \
    && chown office /data

WORKDIR /app
ARG TOKEN_TALLY_VERSION=dev
ARG TOKEN_TALLY_REVISION=
LABEL org.opencontainers.image.version=$TOKEN_TALLY_VERSION \
      org.opencontainers.image.revision=$TOKEN_TALLY_REVISION
RUN python -c 'import json, os; from pathlib import Path; Path("build-info.json").write_text(json.dumps({"schema": 1, "version": os.environ["TOKEN_TALLY_VERSION"], "revision": os.environ["TOKEN_TALLY_REVISION"] or None}) + "\n")'
COPY server.py hook_relay.py LICENSE THIRD-PARTY-NOTICES.md ./
COPY token_tally ./token_tally
COPY static ./static
COPY docker/entrypoint.py /usr/local/bin/office-entrypoint

USER office
VOLUME /data
EXPOSE 8377

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8377/state', timeout=4)"]

ENTRYPOINT ["python", "/usr/local/bin/office-entrypoint"]
