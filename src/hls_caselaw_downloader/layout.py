"""Single source of truth for mapping between static.case.law URLs and local paths.

The mirror layout is a 1:1 copy of the source: every URL under BASE_URL maps to
output_dir / <the URL's path relative to BASE_URL>, and vice versa. Every other
module builds URLs and paths through this module rather than re-deriving the
mapping, so there is exactly one place that knows the on-disk layout.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin

BASE_URL = "https://static.case.law/"

# Case-level formats: stored under <reporter>/<volume>/<subdir>/<case_stem><ext>
CASE_FORMAT_DIRS = {
    "json": "cases",
    "html": "html",
    "case_pdf": "case-pdfs",
}
CASE_FORMAT_EXTS = {
    "json": ".json",
    "html": ".html",
    "case_pdf": ".pdf",
}

# Volume-level bulk formats: stored as a sibling file <reporter>/<volume><ext>
VOLUME_BULK_EXTS = {
    "zip": ".zip",
    "volume_pdf": ".pdf",
    "tar": ".tar",
    "tar_csv": ".tar.csv",
    "tar_sha256": ".tar.sha256",
}

ALL_FORMATS = frozenset(set(CASE_FORMAT_DIRS) | set(VOLUME_BULK_EXTS))

ROOT_METADATA_FILES = (
    "ReportersMetadata.json",
    "VolumesMetadata.json",
    "JurisdictionsMetadata.json",
)


def url_to_local_path(url: str, output_dir: Path) -> Path:
    if not url.startswith(BASE_URL):
        raise ValueError(f"URL {url!r} is not under {BASE_URL!r}")
    relative = url[len(BASE_URL) :]
    return Path(output_dir) / relative


def local_path_to_url(local_path: Path, output_dir: Path) -> str:
    relative = Path(local_path).resolve().relative_to(Path(output_dir).resolve())
    return urljoin(BASE_URL, relative.as_posix())


def root_metadata_url(filename: str) -> str:
    if filename not in ROOT_METADATA_FILES:
        raise ValueError(f"Unknown root metadata file: {filename!r}")
    return BASE_URL + filename


def reporter_metadata_url(reporter: str, filename: str) -> str:
    return f"{BASE_URL}{reporter}/{filename}"


def volume_metadata_url(reporter: str, volume: int, filename: str) -> str:
    return f"{BASE_URL}{reporter}/{volume}/{filename}"


def volume_bulk_url(reporter: str, volume: int, fmt: str) -> str:
    if fmt not in VOLUME_BULK_EXTS:
        raise ValueError(f"Unknown volume bulk format: {fmt!r}")
    return f"{BASE_URL}{reporter}/{volume}{VOLUME_BULK_EXTS[fmt]}"


def case_file_url(reporter: str, volume: int, fmt: str, case_stem: str) -> str:
    if fmt not in CASE_FORMAT_DIRS:
        raise ValueError(f"Unknown case-level format: {fmt!r}")
    subdir = CASE_FORMAT_DIRS[fmt]
    ext = CASE_FORMAT_EXTS[fmt]
    return f"{BASE_URL}{reporter}/{volume}/{subdir}/{case_stem}{ext}"
