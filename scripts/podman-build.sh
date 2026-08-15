#!/usr/bin/env bash
# Build the hls_caselaw_downloader image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${HLS_CASELAW_DOWNLOADER_IMAGE:-hls_caselaw_downloader:latest}"

podman build -t "$IMAGE" -f "$REPO_ROOT/Containerfile" "$REPO_ROOT"
echo "Built $IMAGE"
