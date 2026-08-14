"""Parsing and (cached) fetching of CAP's published metadata index files.

Parsing (`parse_*`) is pure — it operates on already-loaded JSON, so
planner tests can feed it fixture data with no network involved. `RemoteIndex`
is the thin, stateful layer that fetches+caches those files via an injected
`fetch_json` callable (kept decoupled from http_client so it's swappable in
tests).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from caselaw_mirror import layout

FetchJson = Callable[[str], Any]


@dataclass(frozen=True)
class Jurisdiction:
    id: int | None
    name: str | None
    name_long: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Jurisdiction:
        return cls(id=d.get("id"), name=d.get("name"), name_long=d.get("name_long"))


@dataclass(frozen=True)
class Reporter:
    id: int | str
    slug: str
    full_name: str | None
    short_name: str | None
    start_year: int | None
    end_year: int | None
    jurisdictions: tuple[Jurisdiction, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Reporter:
        return cls(
            id=d.get("id"),
            slug=d.get("slug"),
            full_name=d.get("full_name"),
            short_name=d.get("short_name"),
            start_year=d.get("start_year"),
            end_year=d.get("end_year"),
            jurisdictions=tuple(Jurisdiction.from_dict(j) for j in d.get("jurisdictions", [])),
        )


@dataclass(frozen=True)
class Volume:
    volume_number: str
    reporter_slug: str
    start_year: int | None
    end_year: int | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Volume:
        return cls(
            volume_number=str(d["volume_number"]),
            reporter_slug=d.get("reporter_slug", ""),
            start_year=d.get("start_year"),
            end_year=d.get("end_year"),
        )

    @property
    def number(self) -> int:
        return int(self.volume_number)


@dataclass(frozen=True)
class CaseEntry:
    id: int | str
    file_name: str
    name: str | None
    decision_date: str | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaseEntry:
        return cls(
            id=d["id"],
            file_name=d["file_name"],
            name=d.get("name"),
            decision_date=d.get("decision_date"),
        )


def parse_reporters(raw: list[dict[str, Any]]) -> list[Reporter]:
    return [Reporter.from_dict(d) for d in raw]


def parse_volumes(raw: list[dict[str, Any]]) -> list[Volume]:
    return [Volume.from_dict(d) for d in raw]


def parse_cases(raw: list[dict[str, Any]]) -> list[CaseEntry]:
    return [CaseEntry.from_dict(d) for d in raw]


class RemoteIndex:
    """Fetches and in-memory-caches CAP metadata index files for one run.

    Each metadata file is fetched at most once per RemoteIndex instance
    (construct a new one, or call `.clear_cache()`, to force a re-fetch —
    the on-disk TTL cache for repeated CLI invocations lives one layer up,
    in the code that constructs `fetch_json`).
    """

    def __init__(self, fetch_json: FetchJson):
        self._fetch_json = fetch_json
        self._reporters: list[Reporter] | None = None
        self._reporter_volumes: dict[str, list[Volume]] = {}
        self._volume_cases: dict[tuple[str, str], list[CaseEntry]] = {}

    def clear_cache(self) -> None:
        self._reporters = None
        self._reporter_volumes.clear()
        self._volume_cases.clear()

    def get_reporters(self) -> list[Reporter]:
        if self._reporters is None:
            url = layout.root_metadata_url("ReportersMetadata.json")
            self._reporters = parse_reporters(self._fetch_json(url))
        return self._reporters

    def get_reporter_volumes(self, reporter_slug: str) -> list[Volume]:
        if reporter_slug not in self._reporter_volumes:
            url = layout.reporter_metadata_url(reporter_slug, "VolumesMetadata.json")
            self._reporter_volumes[reporter_slug] = parse_volumes(self._fetch_json(url))
        return self._reporter_volumes[reporter_slug]

    def get_volume_cases(self, reporter_slug: str, volume_number: str) -> list[CaseEntry]:
        key = (reporter_slug, volume_number)
        if key not in self._volume_cases:
            url = layout.volume_metadata_url(reporter_slug, int(volume_number), "CasesMetadata.json")
            self._volume_cases[key] = parse_cases(self._fetch_json(url))
        return self._volume_cases[key]
