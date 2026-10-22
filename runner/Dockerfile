# The dapilot runner image: the plugin, as released, plus what it needs to run headless.
#
# This is the host for the hosted tier (docs/hosting.md). It runs the plugin's director
# (run_intake.py) for one seller at a time against a store directory on local disk, which the
# runner copies to and from S3 around each run. The plugin inside the image is this checkout's
# plugins/dapilot, byte for byte, read-only at runtime: a release tag builds the image for that
# release, and nothing in the runner patches, imports or forks it. Anything the runner needs the
# plugin to do differently goes into the plugin as a feature every user gets.
#
# Build from the repository root (the .dockerignore there keeps the context to the plugin and this
# directory):
#   docker build -f runner/Dockerfile --build-arg DAPILOT_RELEASE=$(git describe --tags --always) -t dapilot-runner .
#   docker run --rm dapilot-runner                  # doctor: one line per check, then exits
#
# It runs as a Coolify resource on the EC2 host, built from this repository at a release tag, and
# runs anywhere Docker does. It stays in this repository rather than the 3dapi workers directory so
# that the plugin inside the image is the release, byte for byte.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

# The Claude Code CLI the director spawns workers with (`claude -p`). Pinned so a CLI release
# cannot change worker behaviour under a plugin release that was tested against another one.
ARG CLAUDE_CODE_VERSION=2.1.304
# Where Anthropic publishes the CLI's native binaries: <base>/<version>/manifest.json carries a
# sha256 per platform and <base>/<version>/<platform>/claude is the binary. The same place
# https://claude.ai/install.sh downloads from.
ARG CLAUDE_DOWNLOADS=https://downloads.claude.ai/claude-code-releases
# What the image carries, written to /opt/dapilot/.release for doctor.sh and the run events.
ARG DAPILOT_RELEASE=dev

# What the plugin's own setup_env.py installs on Linux, minus Blender (retired 2026-10-20: every
# render is WebGL in headless Chromium from the studio page the plugin ships). ffmpeg encodes the
# turntable; curl fetches the CLI binary; the fonts keep Chromium from drawing boxes in the studio page.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    chromium \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# A container has no user namespaces, so Chromium's sandbox cannot start even for a non-root user.
# The plugin finds Chrome through $CHROME; pointing that at a wrapper keeps the flag out of the
# plugin, which only adds --no-sandbox on its own when it runs as root. /dev/shm is tiny in a
# container, hence --disable-dev-shm-usage. The capture script adds the SwiftShader flags itself.
RUN printf '#!/bin/sh\nexec /usr/bin/chromium --no-sandbox --disable-dev-shm-usage "$@"\n' \
      > /usr/local/bin/chromium-container \
    && chmod 755 /usr/local/bin/chromium-container

### The Claude Code CLI, as one native binary
# The binary for the pinned version, fetched straight from the release bucket and checked against
# the sha256 the version's manifest names, placed at /usr/local/bin/claude. The installer script
# (claude.ai/install.sh) is not used: it always fetches the newest CLI first and only then has that
# one install the pinned version, which is two downloads of 280 MB where one does, and the build
# then depends on a script that changes under it. The binary is root-owned and read-only like the
# plugin, and the auto-updater is off: a worker could not replace it and must not try. The CLI
# still keeps its state under the runtime user's ~/.claude.
RUN set -eu; \
    case "$(uname -m)" in \
        x86_64|amd64) arch=x64 ;; \
        aarch64|arm64) arch=arm64 ;; \
        *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    platform="linux-${arch}"; \
    curl -fsSL "${CLAUDE_DOWNLOADS}/${CLAUDE_CODE_VERSION}/manifest.json" -o /tmp/claude-manifest.json; \
    checksum=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["platforms"][sys.argv[2]]["checksum"])' \
        /tmp/claude-manifest.json "${platform}"); \
    echo "${checksum}" | grep -Eq '^[0-9a-f]{64}$' || { echo "no sha256 for ${platform} in the manifest" >&2; exit 1; }; \
    curl -fsSL "${CLAUDE_DOWNLOADS}/${CLAUDE_CODE_VERSION}/${platform}/claude" -o /usr/local/bin/claude; \
    echo "${checksum}  /usr/local/bin/claude" | sha256sum -c - >/dev/null; \
    chmod 755 /usr/local/bin/claude; \
    rm /tmp/claude-manifest.json; \
    claude --version
ENV DISABLE_AUTOUPDATER=1

### The plugin, from this checkout
COPY plugins/dapilot /opt/dapilot/plugins/dapilot
RUN echo "${DAPILOT_RELEASE}" > /opt/dapilot/.release

# The pipeline's packages, in a venv the scripts are told about through DAPILOT_PYTHON. Wheels
# only, as setup_env.py does: a missing wheel fails here in seconds, not after compiling scipy.
# The runner's own package (boto3) goes into the same venv; the plugin never imports it.
COPY runner/requirements.txt /tmp/runner-requirements.txt
RUN python3 -m venv /opt/dapilot/.venv \
    && /opt/dapilot/.venv/bin/pip install --no-cache-dir --only-binary=:all: \
       -r /opt/dapilot/plugins/dapilot/requirements.txt -r /tmp/runner-requirements.txt \
    && rm /tmp/runner-requirements.txt

# Root-owned, world-readable only: the workers the director spawns run with permissions skipped,
# and a read-only plugin is what stops one from "fixing" a script mid-run. doctor.sh checks this.
RUN chmod -R a+rX /opt/dapilot

### The runtime user and its directories
# Claude Code refuses the unattended permission mode as root, and the director starts every
# worker in that mode, so everything runs as `runner` (uid 1000, the usual first user, which also
# matches a bind mount made by a laptop user when trying the image by hand).
RUN useradd --uid 1000 --create-home --shell /bin/sh runner \
    && mkdir -p /data/dapilot /app/runner \
    && chown -R runner:runner /data/dapilot /app

WORKDIR /app/runner
COPY --chown=runner:runner runner/doctor.sh runner/consumer.py ./
RUN chmod 755 doctor.sh consumer.py

# The plugin's own environment names (docs/quickstart.md). CLAUDE_PLUGIN_ROOT is what the skills
# use to find the scripts and the studio page; the director exports it to workers as well.
ENV CLAUDE_PLUGIN_ROOT=/opt/dapilot/plugins/dapilot \
    DAPILOT_PLUGIN_DIR=/opt/dapilot/plugins/dapilot \
    DAPILOT_PYTHON=/opt/dapilot/.venv/bin/python \
    CHROME=/usr/local/bin/chromium-container \
    DAPILOT_BILLING=api \
    DAPILOT_DATA_DIR=/data/dapilot \
    DAPILOT_CACHE_DIR=/data/dapilot/.cache \
    PYTHONUNBUFFERED=1

USER runner
ENV HOME=/home/runner

# Where every seller's store directory lives during a run, one subdirectory per seller, each
# pointed at through DAPILOT_STORE_DIR for that seller's jobs. Mount a volume here (the Coolify
# resource does); S3 is the durable copy either way.

# The default command runs every check, renders two frames through the studio page, and then
# starts the queue consumer. A failed check exits 1 before any message is taken, which Coolify
# shows as a restarting container with the FAIL line in its log. `docker run ... ./doctor.sh`
# runs the checks alone and exits; `./doctor.sh --hold` keeps a container up with no queue.
CMD ["./doctor.sh", "--serve"]
