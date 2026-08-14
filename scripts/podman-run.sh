#!/usr/bin/env bash
# Run caselaw-mirror in a container, forwarding all arguments through.
#
#   ./scripts/podman-run.sh plan --config /config/minimal.yaml
#   ./scripts/podman-run.sh sync --config /config/smoke-test.yaml
#
# Config is mounted read-only; the mirror and manifest directories are
# writable volumes. :Z relabels for SELinux hosts (a no-op elsewhere).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${CASELAW_MIRROR_IMAGE:-caselaw-mirror:latest}"

mkdir -p "$REPO_ROOT/mirror" "$REPO_ROOT/manifest"

exec podman run --rm \
  -v "$REPO_ROOT/config:/config:ro,Z" \
  -v "$REPO_ROOT/mirror:/data/mirror:Z" \
  -v "$REPO_ROOT/manifest:/data/manifest:Z" \
  "$IMAGE" "$@"
