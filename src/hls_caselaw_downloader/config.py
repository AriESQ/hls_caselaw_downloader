"""YAML config loading and validation.

Config is deliberately a thin, explicit dataclass tree — no ORM/schema
library — since the shape is small and stable. `load_config` is the only
function that touches YAML; everything downstream works with dataclasses.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hls_caselaw_downloader.layout import ALL_FORMATS
from hls_caselaw_downloader.selection import DEFAULT_FORMATS, Selection, SelectionError

SUPPORTED_CONFIG_VERSION = 1


class ConfigError(ValueError):
    """Raised for malformed or invalid config files."""


@dataclass
class HttpConfig:
    timeout_seconds: int = 30
    max_retries: int = 5
    backoff_factor: float = 1.5
    user_agent: str = "hls_caselaw_downloader/0.1"


@dataclass
class ConcurrencyConfig:
    max_workers: int = 4
    requests_per_second: float = 5.0


@dataclass
class ManifestConfig:
    dir: str = "./manifest"


@dataclass
class Config:
    version: int
    output_dir: str = "./mirror"
    formats: list[str] = field(default_factory=lambda: list(DEFAULT_FORMATS))
    selections: list[Selection] = field(default_factory=list)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    manifest: ManifestConfig = field(default_factory=ManifestConfig)


def _build_section[T](cls: type[T], raw: dict[str, Any], where: str) -> T:
    """Construct a config sub-section, reporting unknown keys as a
    ConfigError rather than letting a raw TypeError escape — a typo (or a
    key removed in a later version) should say which key and what's valid."""
    if not isinstance(raw, dict):
        raise ConfigError(f"Config section '{where}' must be a mapping")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(
            f"Unknown key(s) in '{where}': {sorted(unknown)}. Valid keys: {sorted(known)}"
        )
    return cls(**raw)


def _validate_formats(formats: list[str], where: str) -> None:
    unknown = set(formats) - ALL_FORMATS
    if unknown:
        raise ConfigError(f"Unknown format(s) in {where}: {sorted(unknown)}")


def _build_selection(raw: dict[str, Any], index: int) -> Selection:
    try:
        selection = Selection(
            reporter=raw.get("reporter"),
            jurisdiction=raw.get("jurisdiction"),
            volumes=raw.get("volumes", "all"),
            years=raw.get("years"),
            cases=raw.get("cases"),
            formats=list(raw.get("formats", DEFAULT_FORMATS)),
        )
    except SelectionError as e:
        raise ConfigError(f"selections[{index}]: {e}") from e
    _validate_formats(selection.formats, f"selections[{index}].formats")
    return selection


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        raw_text = path.read_text()
    except OSError as e:
        raise ConfigError(f"Could not read config file {path}: {e}") from e

    try:
        raw: dict[str, Any] = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping at the top level")

    version = raw.get("version")
    if version != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            f"Config file {path} has version={version!r}, "
            f"expected {SUPPORTED_CONFIG_VERSION!r}"
        )

    top_formats = list(raw.get("formats", DEFAULT_FORMATS))
    _validate_formats(top_formats, "formats")

    raw_selections = raw.get("selections", [])
    if not isinstance(raw_selections, list):
        raise ConfigError(f"Config file {path}: 'selections' must be a list")
    selections = [_build_selection(s, i) for i, s in enumerate(raw_selections)]

    concurrency = _build_section(ConcurrencyConfig, raw.get("concurrency", {}), "concurrency")
    http = _build_section(HttpConfig, raw.get("http", {}), "http")
    manifest = _build_section(ManifestConfig, raw.get("manifest", {}), "manifest")

    return Config(
        version=version,
        output_dir=raw.get("output_dir", "./mirror"),
        formats=top_formats,
        selections=selections,
        concurrency=concurrency,
        http=http,
        manifest=manifest,
    )
