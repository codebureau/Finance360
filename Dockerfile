# syntax=docker/dockerfile:1

# ---- Stage 1: build the UI to static files ----
FROM node:25-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: restic and rclone, pinned and checksum-verified (ADR-0011) ----
# Static Go binaries. To upgrade: change the releases and take the new SHA-256 lines from the
# releases' SHA256SUMS files (restic_<v>_linux_<arch>.bz2, rclone-v<v>-linux-<arch>.zip).
# The ARGs are named *_RELEASE on purpose: RCLONE_VERSION / RESTIC_* are option names the tools
# read from the environment, and would break them.
FROM debian:stable-slim AS backup-tools
ARG TARGETARCH
ARG RESTIC_RELEASE=0.19.1
ARG RCLONE_RELEASE=1.75.2
ARG RESTIC_SHA256_AMD64=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
ARG RESTIC_SHA256_ARM64=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
ARG RCLONE_SHA256_AMD64=349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702
ARG RCLONE_SHA256_ARM64=7e1e8d69654941b7b7df84ee74c5f7fb09cce7d5496947fb7bf16b55ff427d10
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bzip2 unzip \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) restic_sha="${RESTIC_SHA256_AMD64}"; rclone_sha="${RCLONE_SHA256_AMD64}" ;; \
      arm64) restic_sha="${RESTIC_SHA256_ARM64}"; rclone_sha="${RCLONE_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    restic_file="restic_${RESTIC_RELEASE}_linux_${TARGETARCH}.bz2"; \
    rclone_dir="rclone-v${RCLONE_RELEASE}-linux-${TARGETARCH}"; \
    cd /tmp; \
    curl -fsSLO "https://github.com/restic/restic/releases/download/v${RESTIC_RELEASE}/${restic_file}"; \
    echo "${restic_sha}  ${restic_file}" | sha256sum -c -; \
    bunzip2 -c "${restic_file}" > /usr/local/bin/restic; \
    curl -fsSLO "https://github.com/rclone/rclone/releases/download/v${RCLONE_RELEASE}/${rclone_dir}.zip"; \
    echo "${rclone_sha}  ${rclone_dir}.zip" | sha256sum -c -; \
    unzip -j "${rclone_dir}.zip" "${rclone_dir}/rclone" -d /usr/local/bin; \
    chmod 0755 /usr/local/bin/restic /usr/local/bin/rclone; \
    restic version; rclone version

# ---- Stage 3: runtime (FastAPI serves the API and the built UI, same origin) ----
FROM python:3.14-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# ca-certificates: rclone talks HTTPS to OneDrive.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=backup-tools /usr/local/bin/restic /usr/local/bin/rclone /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/ ./backend/
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
COPY --from=ui /ui/dist /app/static
# The Guide's Markdown a second time, for the Ask panel to answer from (ADR-0021 section 2).
COPY frontend/src/guide /app/guide
# CONTEXT.md too: Ask's search adds the app's terms to a question that uses another word for them
# (ADR-0023 section 2). Absent, search still works without that.
COPY CONTEXT.md /app/CONTEXT.md

ARG GIT_SHA=dev
# rclone's config lives in its own writable mount (it rewrites the OAuth token on refresh): the
# one deliberate exception to "the data volume is the only writable mount" (ADR-0011).
ENV GIT_SHA=${GIT_SHA} \
    FINANCE360_DATA_DIR=/data \
    FINANCE360_PORT=8000 \
    FINANCE360_STATIC_DIR=/app/static \
    FINANCE360_GUIDE_DIR=/app/guide \
    FINANCE360_CONTEXT_FILE=/app/CONTEXT.md \
    RCLONE_CONFIG=/config/rclone/rclone.conf \
    RCLONE_CACHE_DIR=/tmp/rclone-cache \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin finance360 \
    && mkdir /data /config /config/rclone && chown finance360 /data /config/rclone
USER finance360
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "finance360.main"]
