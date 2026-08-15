from pathlib import Path

import pytest

from hls_caselaw_downloader.planner import plan_all, plan_selection
from hls_caselaw_downloader.remote_index import RemoteIndex
from hls_caselaw_downloader.selection import Selection, SelectionError

OUTPUT_DIR = Path("/data/mirror")


@pytest.fixture
def index(fixture_fetch_json):
    return RemoteIndex(fixture_fetch_json)


def urls(files):
    return [f.url for f in files]


class TestReporterMatching:
    def test_by_slug(self, index):
        files = plan_selection(Selection(reporter="bur"), index, OUTPUT_DIR)
        assert all("/bur/" in u for u in urls(files))

    def test_unknown_reporter_raises(self, index):
        with pytest.raises(SelectionError):
            plan_selection(Selection(reporter="nope"), index, OUTPUT_DIR)

    def test_jurisdiction_that_matches_nothing_raises(self, index):
        with pytest.raises(SelectionError):
            plan_selection(Selection(jurisdiction="Atlantis"), index, OUTPUT_DIR)


class TestFormats:
    def test_json_selection_lists_every_case_in_the_volume(self, index, bur_cases_metadata):
        files = plan_selection(Selection(reporter="bur", formats=["json"]), index, OUTPUT_DIR)
        case_urls = [u for u in urls(files) if "/cases/" in u]
        assert len(case_urls) == len(bur_cases_metadata)
        for case in bur_cases_metadata:
            assert f"https://static.case.law/bur/1/cases/{case['file_name']}.json" in case_urls

    def test_multiple_formats_produce_one_file_each_per_case(self, index, bur_cases_metadata):
        files = plan_selection(
            Selection(reporter="bur", formats=["json", "case_pdf", "html"]), index, OUTPUT_DIR
        )
        all_urls = urls(files)
        for case in bur_cases_metadata:
            stem = case["file_name"]
            assert f"https://static.case.law/bur/1/cases/{stem}.json" in all_urls
            assert f"https://static.case.law/bur/1/case-pdfs/{stem}.pdf" in all_urls
            assert f"https://static.case.law/bur/1/html/{stem}.html" in all_urls

    def test_bulk_formats_do_not_enumerate_cases(self, index):
        # Volume-level archives make per-case listing unnecessary, so
        # CasesMetadata.json shouldn't even be fetched.
        files = plan_selection(Selection(reporter="bur", formats=["tar"]), index, OUTPUT_DIR)
        all_urls = urls(files)
        assert "https://static.case.law/bur/1.tar" in all_urls
        assert not any("/cases/" in u for u in all_urls)
        assert not any("CasesMetadata" in u for u in all_urls)


class TestCaseTargeting:
    def test_explicit_cases_narrow_below_volume(self, index):
        files = plan_selection(
            Selection(reporter="bur", volumes="1", cases=["0003-01"], formats=["json"]),
            index,
            OUTPUT_DIR,
        )
        case_urls = [u for u in urls(files) if "/cases/" in u]
        assert case_urls == ["https://static.case.law/bur/1/cases/0003-01.json"]


class TestVolumeAndYearFiltering:
    def test_volume_range_excludes_non_matching_volumes(self, index):
        files = plan_selection(Selection(reporter="bur", volumes="99"), index, OUTPUT_DIR)
        assert not any("/99" in u for u in urls(files))
        # Reporter-level metadata is still planned even when no volume matches.
        assert "https://static.case.law/bur/VolumesMetadata.json" in urls(files)

    def test_year_range_matching_volume_is_included(self, index):
        # bur volume 1 covers 1842-1843.
        files = plan_selection(Selection(reporter="bur", years="1842-1843"), index, OUTPUT_DIR)
        assert any("/bur/1/" in u for u in urls(files))

    def test_year_range_outside_volume_excludes_it(self, index):
        files = plan_selection(Selection(reporter="bur", years="1900-1950"), index, OUTPUT_DIR)
        assert not any("/bur/1/" in u for u in urls(files))


class TestPlanAll:
    def test_includes_root_metadata_once(self, index):
        files = plan_all(
            [Selection(reporter="bur"), Selection(reporter="bur", formats=["tar"])],
            index,
            OUTPUT_DIR,
        )
        assert urls(files).count("https://static.case.law/ReportersMetadata.json") == 1

    def test_deduplicates_overlapping_selections(self, index):
        once = plan_all([Selection(reporter="bur")], index, OUTPUT_DIR)
        twice = plan_all([Selection(reporter="bur"), Selection(reporter="bur")], index, OUTPUT_DIR)
        assert urls(once) == urls(twice)

    def test_root_metadata_can_be_skipped(self, index):
        files = plan_all([Selection(reporter="bur")], index, OUTPUT_DIR, include_root_metadata=False)
        assert "https://static.case.law/ReportersMetadata.json" not in urls(files)

    def test_local_paths_mirror_the_source_layout(self, index):
        files = plan_all([Selection(reporter="bur")], index, OUTPUT_DIR)
        by_url = {f.url: f.local_path for f in files}
        assert by_url["https://static.case.law/bur/1/cases/0003-01.json"] == (
            OUTPUT_DIR / "bur/1/cases/0003-01.json"
        )
