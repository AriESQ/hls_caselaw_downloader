# caselaw-mirror

Mirror [Harvard's Caselaw Access Project](https://case.law/) (CAP) bulk data from
`static.case.law`, at whatever granularity you need — a single case, a volume range, a
jurisdiction, or the whole corpus — and keep that mirror up to date on re-run.

## About the data source

CAP published ~6.7M U.S. court decisions (~40M pages, 360 years, all state and federal
official reporters through 2020). The dataset is **CC0** (public-domain equivalent).

The old CAPAPI search endpoint was **disabled in September 2024**. The only remaining
access path is the static bulk bucket at `static.case.law`, which needs no API key and
no authentication — it's plain HTTPS GETs against published static files. Existing
Python wrappers (`pyclerk`, `wheatonpeters`, `justopinion`, `harvard-lil/cap-examples`)
all target the dead API, which is why this tool exists.

The upstream data is effectively frozen (content through 2020; last bulk refresh observed
2024-02-28), so "keeping up to date" here means cheaply re-verifying a slow-changing
mirror, not tracking a live feed.

### Source layout

```
static.case.law/
├── ReportersMetadata.json, VolumesMetadata.json, JurisdictionsMetadata.json
└── <reporter>/                       e.g. cal/
    ├── ReporterMetadata.json, VolumesMetadata.json
    ├── <volume>.zip .pdf .tar .tar.csv .tar.sha256    whole-volume artifacts
    └── <volume>/                     e.g. cal/1/
        ├── VolumeMetadata.json, CasesMetadata.json
        ├── cases/<stem>.json         full case text, self-contained
        ├── html/<stem>.html
        └── case-pdfs/<stem>.pdf
```

The local mirror reproduces this layout exactly, so `mirror/cal/1/cases/0009-01.json`
corresponds 1:1 to the source URL.

## Quick start

```bash
# Bare (uv handles the venv; nothing is installed globally)
uv run caselaw-mirror plan --reporter bur --volumes 1 --formats json
uv run caselaw-mirror sync --config config/minimal.yaml

# Containerized (Podman)
./scripts/podman-build.sh
./scripts/podman-run.sh sync --config /config/minimal.yaml
```

## Commands

| Command | Purpose |
| --- | --- |
| `sync` | Download planned files, skipping unchanged ones. `--dry-run` reports what would change without writing; `--limit N` caps files per run. |
| `plan` | Resolve selections and print the exact file list. No downloads. |
| `verify` | Re-checksum local files against the manifest. No network. |
| `status` | Summarize manifest state: files tracked, per-status counts, last change. |
| `list-reporters` | Fetch and print all ~400 reporters. |

## Targeting

The same vocabulary works as YAML `selections:` or as CLI flags (flags are appended to
the config's selections; `--only` replaces them instead).

| Key / flag | Meaning |
| --- | --- |
| `reporter` | Reporter slug or id, or `all`. Never an implicit default — "everything" must be spelled out. |
| `jurisdiction` | e.g. `Cal.`, matched against each reporter's jurisdictions. |
| `volumes` | `"all"`, `"5"`, `"1-50"`, or `"1,3,5-9"`. |
| `years` | `"1850-1900"` — matched against each volume's year range. |
| `cases` | Exact case-file stems, e.g. `["0009-01"]`, to target below the volume level. |
| `formats` | `json`, `html`, `case_pdf`, `volume_pdf`, `zip`, `tar`, `tar_csv`, `tar_sha256`. |

```bash
# One specific case, JSON and PDF
uv run caselaw-mirror sync --reporter cal --volumes 1 --cases 0009-01 --formats json,case_pdf

# A volume range of one reporter
uv run caselaw-mirror sync --reporter cal --volumes 1-50 --formats json

# Whole-volume tar archives (checksum-verified) for a year range
uv run caselaw-mirror sync --reporter cal --years 1850-1900 --formats tar
```

See `config/example.yaml` for the fully commented config schema.

## How "up to date" works

Each run consults the manifest and issues a conditional GET
(`If-None-Match` / `If-Modified-Since`). Unchanged files come back `304` with no body, so
re-syncing a current mirror transfers essentially nothing. Downloads are written to a
`.part` file and then atomically `os.replace()`d, so an interrupted run never leaves a
truncated file that looks complete.

Conditional headers are sent only when the local file actually exists — if a file was
deleted, it re-downloads rather than being skipped as "not modified."

### Integrity

CAP publishes a real checksum only for volume-level tar bundles (`<volume>.tar.sha256`).
Those are verified on download, and a mismatch fails that file (no bytes written) without
aborting the rest of the run.

For every other format there is **no published per-file checksum** — an upstream
limitation, not a gap in this tool. For those, ETag/Last-Modified is the change signal,
and the manifest additionally records git's blob SHA-1
(`sha1("blob " + len + "\0" + content)`) as a stable content identity. That formula is a
pure function of content: it is unaffected by history rewrites (rebase/squash/amend change
commit SHAs, never blob SHAs), and it matches real `git hash-object` output, so it stays
meaningful even though the mirrored data itself is never committed to git. `verify` uses
it to detect local corruption.

## Manifest

SQLite, sharded one file per reporter (`manifest/<reporter_slug>.sqlite3`, with root-level
metadata in `_root.sqlite3`). Reporters range from one volume to 220+, making that a
bounded, navigable unit — as opposed to a single file holding ~6.7M rows, or hundreds of
thousands of per-volume shards.

For a diffable, reviewable snapshot (e.g. to commit alongside a CI run), export to JSONL —
one sorted line per file:

```python
from caselaw_mirror.manifest import export_jsonl, import_jsonl
export_jsonl("./manifest", "manifest.jsonl")
import_jsonl("manifest.jsonl", "./manifest")
```

## Visibility

Long syncs log a periodic heartbeat so a stalled run is distinguishable from a slow one:

```
INFO caselaw_mirror.downloader: starting sync: 45 files planned
INFO caselaw_mirror.downloader: progress: 25/45 done (downloaded=25 skipped=0 failed=0) — elapsed 8s
INFO caselaw_mirror.downloader: sync finished: 45/45 downloaded, 0 skipped, 0 failed, 31307144 bytes, 15.1s
```

Log lines are append-only (no terminal redraw), which keeps them readable in captured CI
logs. `--verbose` adds per-file DEBUG lines; `--json-logs` emits structured JSON.

Exit codes: `0` ok · `1` partial failure (run completed, some files failed after retries) ·
`2` config/selection error · `3` unexpected error.

## Politeness

Requests are rate-limited (default 5/sec, `concurrency.requests_per_second`) with bounded
concurrency and exponential backoff honoring `Retry-After`.

Note that `static.case.law/robots.txt` is a blanket `Disallow: /` — the standard pattern
for keeping an asset bucket out of search indexes, and CAP's own documentation presents
this bucket as the intended bulk-access mechanism. Accordingly this tool performs **no
recursive crawling or discovery**: it only requests URLs derived from CAP's own published
metadata index files, at a modest rate.

## Testing

```bash
uv run pytest              # 120 tests, no network access
uv run ruff check .
./scripts/podman-smoke-test.sh   # end-to-end, bounded live traffic
```

The smoke test targets `bur` (Burnett, 1842–43) — the smallest reporter, one volume of 38
cases — and asserts a re-sync downloads nothing. Mirroring the full corpus is a deliberate,
long-running operation, explicitly out of scope for routine testing.

## Known limitations

- **Year filtering is volume-granular.** CAP publishes no per-case date index at the bulk
  level (`decision_date` lives inside each case's own JSON), so `years` selects volumes
  whose ranges overlap, not individual cases.
- **No per-case checksums upstream** — see [Integrity](#integrity).
- **Dataset ends at 2020.** For newer decisions, see
  [CourtListener](https://www.courtlistener.com/).

## License

Tool code: not yet licensed (TODO). The mirrored CAP data is CC0 (President and Fellows of
Harvard University) and is not covered by this project's license.
