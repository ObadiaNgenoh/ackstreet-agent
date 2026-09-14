# ACKSTREET AGENT — container image.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t ackstreet-agent:latest .
#
# Run:
#   docker run -it --rm \
#     -e OPENAI_API_KEY=sk-... \
#     -v ackstreet-home:/home/ackstreet/.ackstreet \
#     ackstreet-agent:latest chat
#
# State (config, skills, memory, workspace) lives in /home/ackstreet/.ackstreet
# and is meant to be mounted as a volume so the agent keeps what it learns.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ACKSTREET_HOME=/home/ackstreet/.ackstreet \
    PATH="/home/ackstreet/.local/bin:${PATH}"

# Tools the agent is likely to want at runtime: git for clones, curl for
# fetching, and a shell with the usual coreutils (already in the base image).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git \
      curl \
      ca-certificates \
      ripgrep \
      jq \
      less \
 && rm -rf /var/lib/apt/lists/*

# --- dependency layer (cached independently of the source) ----------------
WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- application layer ----------------------------------------------------
COPY pyproject.toml README.md LICENSE ./
COPY ackstreet ./ackstreet
COPY scripts ./scripts
COPY tests ./tests

RUN pip install --no-deps -e .

# --- non-root runtime user ------------------------------------------------
RUN useradd --create-home --shell /bin/bash ackstreet \
 && mkdir -p "${ACKSTREET_HOME}" \
 && chown -R ackstreet:ackstreet /app "${ACKSTREET_HOME}" \
 && su ackstreet -c "ackstreet init" >/dev/null

USER ackstreet

# Keep the interactive session friendly.
ENV TERM=xterm-256color \
    NO_COLOR=""

WORKDIR /home/ackstreet

# `docker run ackstreet-agent` with no arguments opens the health check so a
# misconfigured container explains itself instead of closing silently.
ENTRYPOINT ["ackstreet"]
CMD ["doctor"]
