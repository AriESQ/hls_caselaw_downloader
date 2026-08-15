"""Selection model: expresses what to mirror, independent of YAML vs CLI origin.

One vocabulary, one parser. Both config.py (YAML) and cli.py (flags) build
Selection objects through the same constructors, so there is exactly one
place that understands "1-50", "1,3,5-9", "all", and year-range overlap.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SelectionError(ValueError):
    """Raised for invalid selection expressions or configuration."""


@dataclass(frozen=True)
class RangeMatcher:
    """Matches an integer against a parsed range expression."""

    all_: bool
    values: frozenset[int]

    def matches(self, n: int) -> bool:
        return self.all_ or n in self.values

    def is_bounded(self) -> bool:
        return not self.all_


_ALL = RangeMatcher(all_=True, values=frozenset())


def parse_range_expr(expr: str) -> RangeMatcher:
    """Parse "all", "N", "N-M", or a comma-separated mix into a RangeMatcher."""
    expr = expr.strip()
    if not expr:
        raise SelectionError("Range expression must not be empty")
    if expr == "all":
        return _ALL

    values: set[int] = set()
    for raw_part in expr.split(","):
        part = raw_part.strip()
        if not part:
            raise SelectionError(f"Empty segment in range expression {expr!r}")
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                raise SelectionError(f"Invalid range segment {part!r} in {expr!r}")
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except ValueError as e:
                raise SelectionError(f"Non-integer bound in {part!r}") from e
            if lo > hi:
                raise SelectionError(f"Range {part!r} has lo > hi")
            values.update(range(lo, hi + 1))
        else:
            try:
                values.add(int(part))
            except ValueError as e:
                raise SelectionError(f"Non-integer value {part!r} in {expr!r}") from e

    return RangeMatcher(all_=False, values=frozenset(values))


@dataclass(frozen=True)
class YearRange:
    """Inclusive year bounds, or unbounded (matches everything)."""

    unbounded: bool
    start: int | None = None
    end: int | None = None

    def overlaps(self, vol_start: int | None, vol_end: int | None) -> bool:
        if self.unbounded:
            return True
        if vol_start is None or vol_end is None:
            # Volume metadata missing years — can't evaluate the filter, so
            # don't silently include it; caller decides how to handle this.
            return False
        return vol_start <= (self.end if self.end is not None else vol_start) and vol_end >= (
            self.start if self.start is not None else vol_end
        )


_UNBOUNDED_YEARS = YearRange(unbounded=True)


def parse_year_range(expr: str | None) -> YearRange:
    if expr is None or expr.strip() == "all":
        return _UNBOUNDED_YEARS
    expr = expr.strip()
    if not expr:
        raise SelectionError("Year expression must not be empty")
    if "-" in expr:
        bounds = expr.split("-")
        if len(bounds) != 2:
            raise SelectionError(f"Invalid year range {expr!r}")
        try:
            start, end = int(bounds[0]), int(bounds[1])
        except ValueError as e:
            raise SelectionError(f"Non-integer year bound in {expr!r}") from e
        if start > end:
            raise SelectionError(f"Year range {expr!r} has start > end")
        return YearRange(unbounded=False, start=start, end=end)
    try:
        year = int(expr)
    except ValueError as e:
        raise SelectionError(f"Non-integer year {expr!r}") from e
    return YearRange(unbounded=False, start=year, end=year)


DEFAULT_FORMATS = ("json",)


@dataclass
class Selection:
    """One entry from config `selections:` (or one built from CLI flags)."""

    reporter: str | None = None
    jurisdiction: str | None = None
    volumes: str = "all"
    years: str | None = None
    cases: list[str] | None = None
    formats: list[str] = field(default_factory=lambda: list(DEFAULT_FORMATS))

    def __post_init__(self) -> None:
        if self.reporter is None and self.jurisdiction is None:
            raise SelectionError(
                "Selection must specify at least one of: reporter, jurisdiction"
            )
        if not self.formats:
            raise SelectionError("Selection must specify at least one format")

    def volume_matcher(self) -> RangeMatcher:
        return parse_range_expr(self.volumes)

    def year_range(self) -> YearRange:
        return parse_year_range(self.years)


def resolve_selections(
    config_selections: list[Selection],
    cli_selection: Selection | None,
    only: bool = False,
) -> list[Selection]:
    """Merge a config file's `selections:` list with an ad-hoc CLI selection.

    CLI flags always win when both are given: `only=True` (the CLI's
    `--only` flag) replaces the config's selections entirely; otherwise the
    CLI selection is appended, narrowing/extending what config already asks
    for. Absent any CLI selection, the config's list is used as-is.
    """
    if cli_selection is None:
        return list(config_selections)
    if only:
        return [cli_selection]
    return [*config_selections, cli_selection]
