#!/usr/bin/env bash
# End-to-end smoke test: build the image, sync a deliberately tiny scope
# (the `bur` reporter, 1 volume / 38 cases), then re-sync to prove the
# run is idempotent. Bounded live traffic — never approaches the full
# ~6.7M-case corpus.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="/config/smoke-test.yaml"
LIMIT="${SMOKE_TEST_LIMIT:-50}"

"$REPO_ROOT/scripts/podman-build.sh"

echo "==> plan (no downloads)"
"$REPO_ROOT/scripts/podman-run.sh" plan --config "$CONFIG"

echo "==> sync (real download, limit $LIMIT)"
"$REPO_ROOT/scripts/podman-run.sh" sync --config "$CONFIG" --limit "$LIMIT"

echo "==> re-sync (expect all skipped via 304)"
resync_log="$(mktemp)"
trap 'rm -f "$resync_log"' EXIT
"$REPO_ROOT/scripts/podman-run.sh" sync --config "$CONFIG" --limit "$LIMIT" 2>&1 | tee "$resync_log"

if ! grep -qE '0/[0-9]+ downloaded' "$resync_log"; then
  echo "FAIL: re-sync downloaded files again; expected everything to be skipped via 304" >&2
  exit 1
fi

echo "==> verify (re-checksum local files, no network)"
"$REPO_ROOT/scripts/podman-run.sh" verify --config "$CONFIG"

echo "==> status"
"$REPO_ROOT/scripts/podman-run.sh" status --config "$CONFIG"

file_count="$(find "$REPO_ROOT/mirror" -type f | wc -l | tr -d ' ')"
if [ "$file_count" -lt 1 ]; then
  echo "FAIL: no files landed in $REPO_ROOT/mirror" >&2
  exit 1
fi

echo "PASS: smoke test complete ($file_count files mirrored)"
