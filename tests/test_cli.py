"""CLI tests: the exit-code contract, flag handling, and command wiring.

These matter because the exit codes are the machine-readable interface a
scheduled/CI run would branch on, and because the CLI is where config and
flags get reconciled. HTTP is mocked throughout — no network access.
"""

import json

import pytest
import responses

from caselaw_mirror import exit_codes
from caselaw_mirror.cli import build_cli_selection, build_parser, main
from caselaw_mirror.manifest import ManifestEntry, ShardedManifest

CASE_URL = "https://static.case.law/bur/1/cases/0003-01.json"
BODY = b'{"id": 1, "name": "A v. B"}'


@pytest.fixture
def fast_config(tmp_path):
    """A config with rate limiting disabled.

    The default 5 req/s is right for the real bucket but pure sleep against
    mocked responses, so CLI tests that issue several requests pass this to
    keep the suite fast.
    """
    path = tmp_path / "fast.yaml"
    path.write_text("version: 1\nconcurrency:\n  requests_per_second: 0\n")
    return str(path)


@pytest.fixture
def mock_cap(reporters_metadata, bur_volumes_metadata, bur_cases_metadata):
    """Register every metadata endpoint a `bur` selection touches.

    Covers both resolution (planner reads these) and download (sync also
    fetches them as files), so sync tests don't 404 on metadata.
    """

    def register():
        for url, payload in (
            ("https://static.case.law/ReportersMetadata.json", reporters_metadata),
            ("https://static.case.law/bur/VolumesMetadata.json", bur_volumes_metadata),
            ("https://static.case.law/bur/1/CasesMetadata.json", bur_cases_metadata),
        ):
            responses.add(responses.GET, url, json=payload)
        for url in (
            "https://static.case.law/VolumesMetadata.json",
            "https://static.case.law/JurisdictionsMetadata.json",
            "https://static.case.law/bur/ReporterMetadata.json",
            "https://static.case.law/bur/1/VolumeMetadata.json",
        ):
            responses.add(responses.GET, url, json=[])

    return register


class TestBuildCliSelection:
    def test_returns_none_without_a_target(self):
        args = build_parser().parse_args(["sync", "--volumes", "1-5"])
        assert build_cli_selection(args) is None

    def test_builds_from_flags(self):
        args = build_parser().parse_args(
            ["sync", "--reporter", "cal", "--volumes", "1-5", "--years", "1850-1900"]
        )
        selection = build_cli_selection(args)
        assert selection.reporter == "cal"
        assert selection.volumes == "1-5"
        assert selection.years == "1850-1900"

    def test_splits_comma_separated_lists(self):
        args = build_parser().parse_args(
            ["sync", "--reporter", "cal", "--formats", "json, case_pdf", "--cases", "0009-01,0015-01"]
        )
        selection = build_cli_selection(args)
        assert selection.formats == ["json", "case_pdf"]
        assert selection.cases == ["0009-01", "0015-01"]

    def test_jurisdiction_alone_is_a_valid_target(self):
        args = build_parser().parse_args(["sync", "--jurisdiction", "Cal."])
        assert build_cli_selection(args).jurisdiction == "Cal."


class TestExitCodes:
    """The machine-readable contract — a CI job branches on these."""

    def test_config_error_for_missing_config_file(self, tmp_path):
        assert main(["status", "--config", str(tmp_path / "nope.yaml")]) == exit_codes.CONFIG_ERROR

    def test_config_error_for_unknown_config_key(self, tmp_path):
        config = tmp_path / "c.yaml"
        config.write_text("version: 1\nmanifest:\n  format: sqlite\n")
        assert main(["status", "--config", str(config)]) == exit_codes.CONFIG_ERROR

    def test_config_error_when_no_selection_given(self, tmp_path):
        assert main(["plan", "--output-dir", str(tmp_path)]) == exit_codes.CONFIG_ERROR

    @responses.activate
    def test_config_error_for_unknown_reporter(self, tmp_path, mock_cap, fast_config):
        mock_cap()
        assert main(["plan", "--config", fast_config, "--reporter", "nope", "--output-dir", str(tmp_path)]) == (
            exit_codes.CONFIG_ERROR
        )

    @responses.activate
    def test_ok_on_successful_sync(self, tmp_path, mock_cap, fast_config):
        mock_cap()
        responses.add(responses.GET, CASE_URL, body=BODY, status=200)
        code = main(
            [
                "sync",
                "--config",
                fast_config,
                "--reporter",
                "bur",
                "--cases",
                "0003-01",
                "--output-dir",
                str(tmp_path / "mirror"),
                "--manifest-dir",
                str(tmp_path / "manifest"),
            ]
        )
        assert code == exit_codes.OK

    @responses.activate
    def test_partial_failure_when_a_file_404s(self, tmp_path, mock_cap, fast_config):
        mock_cap()
        responses.add(responses.GET, CASE_URL, status=404)
        code = main(
            [
                "sync",
                "--config",
                fast_config,
                "--reporter",
                "bur",
                "--cases",
                "0003-01",
                "--output-dir",
                str(tmp_path / "mirror"),
                "--manifest-dir",
                str(tmp_path / "manifest"),
            ]
        )
        assert code == exit_codes.PARTIAL_FAILURE

    def test_fatal_for_unexpected_errors(self, tmp_path, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("caselaw_mirror.cli.ShardedManifest", boom)
        assert main(["status", "--manifest-dir", str(tmp_path)]) == exit_codes.FATAL


class TestPathOverrides:
    def test_cli_paths_override_config(self, tmp_path):
        config = tmp_path / "c.yaml"
        config.write_text("version: 1\noutput_dir: /from/config\nmanifest:\n  dir: /from/config\n")
        override = tmp_path / "override"
        # status reports the manifest dir it actually used.
        assert main(["status", "--config", str(config), "--manifest-dir", str(override)]) == (
            exit_codes.OK
        )


class TestStatusAndVerify:
    def test_status_on_empty_manifest(self, tmp_path, capsys):
        assert main(["status", "--manifest-dir", str(tmp_path)]) == exit_codes.OK
        assert "No manifest entries" in capsys.readouterr().out

    def test_status_summarizes_entries(self, tmp_path, capsys):
        with ShardedManifest(tmp_path / "manifest") as m:
            m.upsert(
                ManifestEntry(
                    url=CASE_URL,
                    local_path=str(tmp_path / "f.json"),
                    format="json",
                    status="ok",
                    size_bytes=42,
                    last_changed_at="2026-01-01T00:00:00+00:00",
                )
            )
        assert main(["status", "--manifest-dir", str(tmp_path / "manifest")]) == exit_codes.OK
        out = capsys.readouterr().out
        assert "1 files tracked" in out
        assert "ok: 1" in out
        assert "42" in out

    def test_verify_reports_missing_file_as_partial_failure(self, tmp_path, capsys):
        with ShardedManifest(tmp_path / "manifest") as m:
            m.upsert(
                ManifestEntry(
                    url=CASE_URL,
                    local_path=str(tmp_path / "gone.json"),
                    format="json",
                    status="ok",
                    git_blob_sha1="a" * 40,
                )
            )
        code = main(["verify", "--manifest-dir", str(tmp_path / "manifest")])
        assert code == exit_codes.PARTIAL_FAILURE
        assert "MISSING" in capsys.readouterr().out


class TestManifestCommand:
    def test_export_then_import_round_trip(self, tmp_path):
        src = tmp_path / "manifest"
        with ShardedManifest(src) as m:
            m.upsert(
                ManifestEntry(url=CASE_URL, local_path=str(tmp_path / "f.json"), format="json", status="ok")
            )
        jsonl = tmp_path / "m.jsonl"

        assert main(["manifest", "export", "--manifest-dir", str(src), "--to", str(jsonl)]) == (
            exit_codes.OK
        )
        assert json.loads(jsonl.read_text().strip())["url"] == CASE_URL

        dst = tmp_path / "manifest2"
        assert main(["manifest", "import", "--manifest-dir", str(dst), "--from", str(jsonl)]) == (
            exit_codes.OK
        )
        with ShardedManifest(dst) as m2:
            assert m2.get(CASE_URL) is not None


class TestPlanCommand:
    @responses.activate
    def test_plan_lists_urls_without_downloading(self, tmp_path, mock_cap, capsys, fast_config):
        mock_cap()
        code = main(["plan", "--config", fast_config, "--reporter", "bur", "--formats", "json",
                    "--output-dir", str(tmp_path)])
        assert code == exit_codes.OK
        out = capsys.readouterr().out
        assert CASE_URL in out
        # Nothing should have been written to the mirror.
        assert not (tmp_path / "bur").exists()

    @responses.activate
    def test_only_flag_replaces_config_selections(self, tmp_path, mock_cap, capsys):
        mock_cap()
        config = tmp_path / "c.yaml"
        config.write_text("version: 1\nselections:\n  - reporter: cal\n    volumes: '1'\n")
        code = main(
            [
                "plan",
                "--config",
                str(config),
                "--only",
                "--reporter",
                "bur",
                "--output-dir",
                str(tmp_path),
            ]
        )
        assert code == exit_codes.OK
        out = capsys.readouterr().out
        assert "/bur/" in out
        assert "/cal/" not in out


class TestListReporters:
    @responses.activate
    def test_prints_reporters(self, reporters_metadata, capsys):
        responses.add(
            responses.GET, "https://static.case.law/ReportersMetadata.json", json=reporters_metadata
        )
        assert main(["list-reporters"]) == exit_codes.OK
        assert "bur" in capsys.readouterr().out


class TestDryRun:
    @responses.activate
    def test_dry_run_writes_nothing(self, tmp_path, mock_cap, fast_config):
        mock_cap()
        responses.add(responses.GET, CASE_URL, body=BODY, status=200)
        mirror = tmp_path / "mirror"
        manifest_dir = tmp_path / "manifest"
        code = main(
            [
                "sync",
                "--config",
                fast_config,
                "--dry-run",
                "--reporter",
                "bur",
                "--cases",
                "0003-01",
                "--output-dir",
                str(mirror),
                "--manifest-dir",
                str(manifest_dir),
            ]
        )
        assert code == exit_codes.OK
        assert not (mirror / "bur").exists()
        with ShardedManifest(manifest_dir) as m:
            assert m.get(CASE_URL) is None
