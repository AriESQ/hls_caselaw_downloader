# Podman-native Containerfile (Docker-compatible syntax, so a future CI job
# can build it either way).
FROM python:3.12-slim

# uv gives us the same locked dependency resolution in the container as
# locally, from the committed uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first: this only re-resolves when the lockfile or
# project metadata changes, not on every source edit.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Run as a non-root user. Matters for rootless Podman's UID mapping and
# means a compromised scrape can't write outside its mounts.
RUN groupadd --gid 1000 caselaw \
    && useradd --uid 1000 --gid 1000 --create-home caselaw \
    && mkdir -p /data/mirror /data/manifest \
    && chown -R caselaw:caselaw /app /data
USER caselaw

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "-m", "caselaw_mirror"]
# A bare `podman run` should be self-documenting rather than an arg error.
CMD ["--help"]
