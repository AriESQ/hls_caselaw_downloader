"""Turns a Selection (+ remote index data) into a concrete list of files to sync.

Pure with respect to network I/O: `plan_selection` takes an already-populated
`RemoteIndex` (whose fetch calls are cached, and in tests can be backed by
fixture data) and returns a deterministic list of RemoteFile. No I/O happens
in this module itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from caselaw_mirror import layout
from caselaw_mirror.remote_index import RemoteIndex, Reporter, Volume
from caselaw_mirror.selection import Selection, SelectionError

CASE_FORMATS = frozenset(layout.CASE_FORMAT_DIRS)
VOLUME_BULK_FORMATS = frozenset(layout.VOLUME_BULK_EXTS)


@dataclass(frozen=True)
class RemoteFile:
    url: str
    local_path: Path
    format: str  # one of layout.ALL_FORMATS, or "metadata"


def reporter_in_jurisdiction(reporter: Reporter, jurisdiction: str) -> bool:
    """Match a reporter against a jurisdiction's short or long name, case-insensitively.

    Shared with `list-reporters --jurisdiction` so browsing and selecting agree
    on what counts as a match.
    """
    code = jurisdiction.strip().lower()
    return any((j.name or "").lower() == code or (j.name_long or "").lower() == code for j in reporter.jurisdictions)


def _select_reporters(selection: Selection, reporters: list[Reporter]) -> list[Reporter]:
    matched = reporters
    if selection.jurisdiction is not None:
        matched = [r for r in matched if reporter_in_jurisdiction(r, selection.jurisdiction)]
    if selection.reporter is not None and selection.reporter != "all":
        target = selection.reporter.strip().lower()
        matched = [r for r in matched if r.slug.lower() == target or str(r.id) == selection.reporter]
    elif selection.reporter is None and selection.jurisdiction is None:
        raise SelectionError("Selection resolved no reporters (need reporter or jurisdiction)")

    if not matched:
        raise SelectionError(
            f"No reporters matched selection (reporter={selection.reporter!r}, "
            f"jurisdiction={selection.jurisdiction!r})"
        )
    return matched


def _select_volumes(selection: Selection, volumes: list[Volume]) -> list[Volume]:
    volume_matcher = selection.volume_matcher()
    year_range = selection.year_range()
    matched = [
        v
        for v in volumes
        if volume_matcher.matches(v.number) and year_range.overlaps(v.start_year, v.end_year)
    ]
    return matched


def _metadata_file(reporter: Reporter, volume: Volume, filename: str) -> RemoteFile:
    url = layout.volume_metadata_url(reporter.slug, volume.number, filename)
    return RemoteFile(url=url, local_path=Path(), format="metadata")


def plan_root_metadata() -> list[RemoteFile]:
    return [
        RemoteFile(url=layout.root_metadata_url(name), local_path=Path(), format="metadata")
        for name in layout.ROOT_METADATA_FILES
    ]


def plan_reporter_metadata(reporter: Reporter) -> list[RemoteFile]:
    return [
        RemoteFile(
            url=layout.reporter_metadata_url(reporter.slug, name),
            local_path=Path(),
            format="metadata",
        )
        for name in ("ReporterMetadata.json", "VolumesMetadata.json")
    ]


def plan_selection(selection: Selection, index: RemoteIndex, output_dir: Path) -> list[RemoteFile]:
    output_dir = Path(output_dir)
    files: list[RemoteFile] = []
    seen_reporter_metadata: set[str] = set()

    reporters = _select_reporters(selection, index.get_reporters())
    case_formats = [f for f in selection.formats if f in CASE_FORMATS]
    bulk_formats = [f for f in selection.formats if f in VOLUME_BULK_FORMATS]

    for reporter in reporters:
        if reporter.slug not in seen_reporter_metadata:
            files.extend(plan_reporter_metadata(reporter))
            seen_reporter_metadata.add(reporter.slug)

        volumes = _select_volumes(selection, index.get_reporter_volumes(reporter.slug))
        for volume in volumes:
            files.append(_metadata_file(reporter, volume, "VolumeMetadata.json"))

            for fmt in bulk_formats:
                url = layout.volume_bulk_url(reporter.slug, volume.number, fmt)
                files.append(RemoteFile(url=url, local_path=Path(), format=fmt))

            if not case_formats:
                continue

            files.append(_metadata_file(reporter, volume, "CasesMetadata.json"))

            if selection.cases:
                stems = list(selection.cases)
            else:
                stems = [c.file_name for c in index.get_volume_cases(reporter.slug, volume.volume_number)]

            for stem in stems:
                for fmt in case_formats:
                    url = layout.case_file_url(reporter.slug, volume.number, fmt, stem)
                    files.append(RemoteFile(url=url, local_path=Path(), format=fmt))

    return [_with_local_path(f, output_dir) for f in files]


def _with_local_path(remote_file: RemoteFile, output_dir: Path) -> RemoteFile:
    local_path = layout.url_to_local_path(remote_file.url, output_dir)
    return RemoteFile(url=remote_file.url, local_path=local_path, format=remote_file.format)


def plan_all(
    selections: list[Selection], index: RemoteIndex, output_dir: Path, include_root_metadata: bool = True
) -> list[RemoteFile]:
    """Plan multiple selections, deduplicating by URL and optionally
    prefixing the run-wide root metadata files (fetched once regardless of
    how many selections reference them)."""
    output_dir = Path(output_dir)
    seen: dict[str, RemoteFile] = {}

    if include_root_metadata:
        for f in plan_root_metadata():
            seen[f.url] = _with_local_path(f, output_dir)

    for selection in selections:
        for f in plan_selection(selection, index, output_dir):
            seen[f.url] = f

    return list(seen.values())
