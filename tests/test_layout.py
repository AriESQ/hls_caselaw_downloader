from pathlib import Path

import pytest

from hls_caselaw_downloader import layout

OUTPUT_DIR = Path("/data/mirror")


class TestUrlBuilders:
    def test_root_metadata(self):
        assert layout.root_metadata_url("ReportersMetadata.json") == (
            "https://static.case.law/ReportersMetadata.json"
        )

    def test_root_metadata_rejects_unknown(self):
        with pytest.raises(ValueError):
            layout.root_metadata_url("NotAThing.json")

    def test_reporter_and_volume_metadata(self):
        assert layout.reporter_metadata_url("cal", "VolumesMetadata.json") == (
            "https://static.case.law/cal/VolumesMetadata.json"
        )
        assert layout.volume_metadata_url("cal", 1, "CasesMetadata.json") == (
            "https://static.case.law/cal/1/CasesMetadata.json"
        )

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [
            ("zip", "https://static.case.law/cal/1.zip"),
            ("volume_pdf", "https://static.case.law/cal/1.pdf"),
            ("tar", "https://static.case.law/cal/1.tar"),
            ("tar_csv", "https://static.case.law/cal/1.tar.csv"),
            ("tar_sha256", "https://static.case.law/cal/1.tar.sha256"),
        ],
    )
    def test_volume_bulk_formats_are_siblings_of_the_volume_dir(self, fmt, expected):
        assert layout.volume_bulk_url("cal", 1, fmt) == expected

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [
            ("json", "https://static.case.law/cal/1/cases/0009-01.json"),
            ("html", "https://static.case.law/cal/1/html/0009-01.html"),
            ("case_pdf", "https://static.case.law/cal/1/case-pdfs/0009-01.pdf"),
        ],
    )
    def test_case_formats_live_in_their_own_subdirs(self, fmt, expected):
        assert layout.case_file_url("cal", 1, fmt, "0009-01") == expected

    def test_case_builder_rejects_bulk_format(self):
        with pytest.raises(ValueError):
            layout.case_file_url("cal", 1, "tar", "0009-01")

    def test_bulk_builder_rejects_case_format(self):
        with pytest.raises(ValueError):
            layout.volume_bulk_url("cal", 1, "json")


class TestPathMapping:
    def test_url_to_local_path_mirrors_source_layout(self):
        url = "https://static.case.law/cal/1/cases/0009-01.json"
        assert layout.url_to_local_path(url, OUTPUT_DIR) == (
            OUTPUT_DIR / "cal/1/cases/0009-01.json"
        )

    def test_url_to_local_path_rejects_foreign_host(self):
        with pytest.raises(ValueError):
            layout.url_to_local_path("https://example.com/evil.json", OUTPUT_DIR)

    @pytest.mark.parametrize(
        "url",
        [
            "https://static.case.law/ReportersMetadata.json",
            "https://static.case.law/cal/VolumesMetadata.json",
            "https://static.case.law/cal/1/VolumeMetadata.json",
            "https://static.case.law/cal/1/cases/0009-01.json",
            "https://static.case.law/cal/1/html/0009-01.html",
            "https://static.case.law/cal/1/case-pdfs/0009-01.pdf",
            "https://static.case.law/cal/1.zip",
            "https://static.case.law/cal/1.tar.sha256",
        ],
    )
    def test_round_trip(self, tmp_path, url):
        local = layout.url_to_local_path(url, tmp_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.touch()  # resolve() needs the path to exist
        assert layout.local_path_to_url(local, tmp_path) == url


def test_all_formats_covers_both_families():
    assert set(layout.CASE_FORMAT_DIRS) | set(layout.VOLUME_BULK_EXTS) == layout.ALL_FORMATS
    # Every case format has a matching extension entry.
    assert set(layout.CASE_FORMAT_DIRS) == set(layout.CASE_FORMAT_EXTS)
