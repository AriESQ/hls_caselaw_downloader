#!/usr/bin/env bash
# Build the caselaw-mirror image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${CASELAW_MIRROR_IMAGE:-caselaw-mirror:latest}"

podman build -t "$IMAGE" -f "$REPO_ROOT/Containerfile" "$REPO_ROOT"
echo "Built $IMAGE"
