import pytest

from hls_caselaw_downloader.selection import (
    Selection,
    SelectionError,
    parse_range_expr,
    parse_year_range,
    resolve_selections,
)


class TestParseRangeExpr:
    def test_all(self):
        m = parse_range_expr("all")
        assert m.matches(1)
        assert m.matches(9999)
        assert not m.is_bounded()

    def test_single_value(self):
        m = parse_range_expr("5")
        assert m.matches(5)
        assert not m.matches(4)

    def test_inclusive_range(self):
        m = parse_range_expr("1-50")
        assert m.matches(1)
        assert m.matches(50)  # inclusive on both ends
        assert not m.matches(51)

    def test_comma_and_range_mix(self):
        m = parse_range_expr("1,3,5-9")
        assert [n for n in range(12) if m.matches(n)] == [1, 3, 5, 6, 7, 8, 9]

    def test_whitespace_tolerated(self):
        assert parse_range_expr(" 1 , 3 ").matches(3)

    @pytest.mark.parametrize("expr", ["", "  ", "abc", "1-", "1-2-3", "5-1", "1,,2"])
    def test_invalid_expressions_raise(self, expr):
        with pytest.raises(SelectionError):
            parse_range_expr(expr)


class TestParseYearRange:
    def test_none_and_all_are_unbounded(self):
        assert parse_year_range(None).unbounded
        assert parse_year_range("all").unbounded

    def test_single_year_overlaps_only_itself(self):
        y = parse_year_range("1850")
        assert y.overlaps(1850, 1850)
        assert not y.overlaps(1851, 1852)

    def test_range_overlaps_partial(self):
        y = parse_year_range("1850-1900")
        assert y.overlaps(1840, 1860)  # straddles the start
        assert y.overlaps(1890, 1910)  # straddles the end
        assert y.overlaps(1860, 1870)  # fully inside
        assert not y.overlaps(1901, 1950)
        assert not y.overlaps(1800, 1849)

    def test_missing_volume_years_do_not_match_a_bounded_filter(self):
        # A volume with no year metadata can't be shown to satisfy the
        # filter, so it must not be silently included.
        assert not parse_year_range("1850-1900").overlaps(None, None)
        # ...but an unbounded filter still takes everything.
        assert parse_year_range(None).overlaps(None, None)

    @pytest.mark.parametrize("expr", ["", "abc", "1900-1850"])
    def test_invalid_year_expressions_raise(self, expr):
        with pytest.raises(SelectionError):
            parse_year_range(expr)


class TestSelection:
    def test_requires_reporter_or_jurisdiction(self):
        with pytest.raises(SelectionError):
            Selection()

    def test_requires_at_least_one_format(self):
        with pytest.raises(SelectionError):
            Selection(reporter="cal", formats=[])

    def test_defaults_to_json(self):
        assert Selection(reporter="cal").formats == ["json"]


class TestResolveSelections:
    def test_config_only(self):
        config = [Selection(reporter="cal")]
        assert resolve_selections(config, None) == config

    def test_cli_appends_by_default(self):
        config = [Selection(reporter="cal")]
        cli = Selection(reporter="bur")
        result = resolve_selections(config, cli)
        assert len(result) == 2
        assert result[-1] is cli

    def test_only_flag_replaces_config(self):
        config = [Selection(reporter="cal")]
        cli = Selection(reporter="bur")
        assert resolve_selections(config, cli, only=True) == [cli]
