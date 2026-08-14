"""Downloader/http tests with mocked HTTP — never touches static.case.law."""

from pathlib import Path

import pytest
import responses

from caselaw_mirror.checksum import git_blob_sha1, sha256_hex
from caselaw_mirror.config import HttpConfig
from caselaw_mirror.downloader import download_one, sync_files, verify_local_files
from caselaw_mirror.http_client import HttpClient
from caselaw_mirror.manifest import ManifestEntry, ShardedManifest
from caselaw_mirror.planner import RemoteFile

CASE_URL = "https://static.case.law/cal/1/cases/0009-01.json"
TAR_URL = "https://static.case.law/cal/1.tar"
BODY = b'{"id": 6679929, "name": "The People vs. Smith"}'


@pytest.fixture
def client():
    # No rate limiting in tests: these are mocked responses, and sleeping
    # between them would only slow the suite down.
    c = HttpClient(HttpConfig(max_retries=0), requests_per_second=0)
    yield c
    c.close()


@pytest.fixture
def manifest(tmp_path):
    m = ShardedManifest(tmp_path / "manifest")
    yield m
    m.close()


def case_file(tmp_path, url=CASE_URL, fmt="json"):
    return RemoteFile(url=url, local_path=tmp_path / "mirror" / "0009-01.json", format=fmt)


class TestDownloadOne:
    @responses.activate
    def test_first_fetch_writes_file_and_records_manifest(self, client, manifest, tmp_path):
        responses.add(responses.GET, CASE_URL, body=BODY, status=200, headers={"ETag": '"v1"'})
        rf = case_file(tmp_path)

        result = download_one(client, manifest, rf)

        assert result.status == "downloaded"
        assert rf.local_path.read_bytes() == BODY
        entry = manifest.get(CASE_URL)
        assert entry.status == "ok"
        assert entry.etag == '"v1"'
        assert entry.git_blob_sha1 == git_blob_sha1(BODY)
        assert entry.size_bytes == len(BODY)

    @responses.activate
    def test_sends_conditional_headers_when_file_already_present(self, client, manifest, tmp_path):
        rf = case_file(tmp_path)
        rf.local_path.parent.mkdir(parents=True, exist_ok=True)
        rf.local_path.write_bytes(BODY)
        manifest.upsert(
            ManifestEntry(
                url=CASE_URL,
                local_path=str(rf.local_path),
                format="json",
                etag='"v1"',
                last_modified="Wed, 28 Feb 2024 00:00:00 GMT",
                status="ok",
            )
        )
        responses.add(responses.GET, CASE_URL, status=304)

        result = download_one(client, manifest, rf)

        assert result.status == "skipped_not_modified"
        sent = responses.calls[0].request.headers
        assert sent["If-None-Match"] == '"v1"'
        assert sent["If-Modified-Since"] == "Wed, 28 Feb 2024 00:00:00 GMT"

    @responses.activate
    def test_no_conditional_headers_when_local_file_is_missing(self, client, manifest, tmp_path):
        """A manifest entry whose file was deleted must re-download, not 304."""
        rf = case_file(tmp_path)
        manifest.upsert(
            ManifestEntry(url=CASE_URL, local_path=str(rf.local_path), format="json", etag='"v1"', status="ok")
        )
        responses.add(responses.GET, CASE_URL, body=BODY, status=200)

        result = download_one(client, manifest, rf)

        assert result.status == "downloaded"
        assert "If-None-Match" not in responses.calls[0].request.headers

    @responses.activate
    def test_http_error_is_recorded_and_reported(self, client, manifest, tmp_path):
        responses.add(responses.GET, CASE_URL, status=404)
        rf = case_file(tmp_path)

        result = download_one(client, manifest, rf)

        assert result.status == "failed"
        assert manifest.get(CASE_URL).status == "http_error"
        assert not rf.local_path.exists()

    @responses.activate
    def test_partial_file_is_not_left_behind_on_failure(self, client, manifest, tmp_path):
        responses.add(responses.GET, CASE_URL, status=500)
        rf = case_file(tmp_path)

        download_one(client, manifest, rf)

        assert not rf.local_path.with_name(rf.local_path.name + ".part").exists()

    @responses.activate
    def test_dry_run_writes_nothing(self, client, manifest, tmp_path):
        responses.add(responses.GET, CASE_URL, body=BODY, status=200, headers={"ETag": '"v1"'})
        rf = case_file(tmp_path)

        result = download_one(client, manifest, rf, dry_run=True)

        assert result.status == "downloaded"  # reports what *would* happen
        assert not rf.local_path.exists()
        assert manifest.get(CASE_URL) is None


class TestTarChecksumVerification:
    @responses.activate
    def test_matching_published_checksum_is_recorded(self, client, manifest, tmp_path):
        tar_body = b"tar bytes"
        responses.add(responses.GET, TAR_URL, body=tar_body, status=200)
        responses.add(
            responses.GET, TAR_URL + ".sha256", body=f"{sha256_hex(tar_body)}  1.tar\n", status=200
        )
        rf = RemoteFile(url=TAR_URL, local_path=tmp_path / "1.tar", format="tar")

        result = download_one(client, manifest, rf)

        assert result.status == "downloaded"
        assert manifest.get(TAR_URL).sha256 == sha256_hex(tar_body)

    @responses.activate
    def test_mismatched_checksum_fails_and_writes_no_file(self, client, manifest, tmp_path):
        responses.add(responses.GET, TAR_URL, body=b"corrupted", status=200)
        responses.add(responses.GET, TAR_URL + ".sha256", body=f"{'0' * 64}  1.tar\n", status=200)
        rf = RemoteFile(url=TAR_URL, local_path=tmp_path / "1.tar", format="tar")

        result = download_one(client, manifest, rf)

        assert result.status == "failed"
        assert not rf.local_path.exists()
        assert manifest.get(TAR_URL).status == "checksum_failed"


class TestSyncFiles:
    @responses.activate
    def test_summary_counts_and_limit(self, client, manifest, tmp_path):
        files = []
        for i in range(5):
            url = f"https://static.case.law/cal/1/cases/{i:04d}-01.json"
            responses.add(responses.GET, url, body=BODY, status=200)
            files.append(RemoteFile(url=url, local_path=tmp_path / f"{i}.json", format="json"))

        summary = sync_files(client, manifest, files, max_workers=2, limit=3)

        assert summary.planned == 3
        assert summary.downloaded == 3
        assert summary.failed == 0
        assert summary.ok

    @responses.activate
    def test_one_failure_does_not_abort_the_run(self, client, manifest, tmp_path):
        ok_url = "https://static.case.law/cal/1/cases/0001-01.json"
        bad_url = "https://static.case.law/cal/1/cases/0002-01.json"
        responses.add(responses.GET, ok_url, body=BODY, status=200)
        responses.add(responses.GET, bad_url, status=404)
        files = [
            RemoteFile(url=ok_url, local_path=tmp_path / "ok.json", format="json"),
            RemoteFile(url=bad_url, local_path=tmp_path / "bad.json", format="json"),
        ]

        summary = sync_files(client, manifest, files, max_workers=1)

        assert summary.downloaded == 1
        assert summary.failed == 1
        assert not summary.ok  # drives the PARTIAL_FAILURE exit code


class TestVerifyLocalFiles:
    def test_detects_missing_and_corrupted_files(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_bytes(BODY)
        corrupted = tmp_path / "corrupted.json"
        corrupted.write_bytes(b"tampered")

        entries = [
            ManifestEntry(
                url="u1", local_path=str(good), format="json", git_blob_sha1=git_blob_sha1(BODY), status="ok"
            ),
            ManifestEntry(
                url="u2",
                local_path=str(corrupted),
                format="json",
                git_blob_sha1=git_blob_sha1(BODY),
                status="ok",
            ),
            ManifestEntry(
                url="u3",
                local_path=str(tmp_path / "gone.json"),
                format="json",
                git_blob_sha1=git_blob_sha1(BODY),
                status="ok",
            ),
        ]

        result = verify_local_files(entries)

        assert result.checked == 3
        assert result.ok == 1
        assert result.mismatched == ["u2"]
        assert result.missing == ["u3"]

    def test_skips_entries_that_never_downloaded_cleanly(self, tmp_path):
        entries = [ManifestEntry(url="u1", local_path=str(tmp_path / "x"), format="json", status="http_error")]
        assert verify_local_files(entries).checked == 0


class TestRateLimiter:
    def test_zero_rate_disables_throttling(self):
        from caselaw_mirror.http_client import RateLimiter

        limiter = RateLimiter(0)
        limiter.acquire()
        limiter.acquire()  # must not hang

    def test_enforces_minimum_interval(self):
        import time

        from caselaw_mirror.http_client import RateLimiter

        limiter = RateLimiter(50)  # 20ms apart
        start = time.monotonic()
        limiter.acquire()
        limiter.acquire()
        assert time.monotonic() - start >= 0.015


def test_local_paths_are_created_as_needed(tmp_path):
    """Nested case dirs (cal/1/cases/) don't exist before the first write."""
    from caselaw_mirror.downloader import _atomic_write

    target = Path(tmp_path) / "cal" / "1" / "cases" / "0009-01.json"
    _atomic_write(target, BODY)
    assert target.read_bytes() == BODY
