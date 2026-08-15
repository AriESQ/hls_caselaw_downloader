import pytest

from hls_caselaw_downloader.manifest import (
    ManifestEntry,
    ShardedManifest,
    export_jsonl,
    import_jsonl,
    shard_key_for_url,
)

CASE_URL = "https://static.case.law/cal/1/cases/0009-01.json"


def make_entry(url=CASE_URL, **overrides):
    defaults = {
        "url": url,
        "local_path": "/data/mirror/cal/1/cases/0009-01.json",
        "format": "json",
        "etag": '"abc123"',
        "status": "ok",
    }
    return ManifestEntry(**{**defaults, **overrides})


class TestShardKey:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://static.case.law/ReportersMetadata.json", "_root"),
            ("https://static.case.law/VolumesMetadata.json", "_root"),
            ("https://static.case.law/JurisdictionsMetadata.json", "_root"),
            ("https://static.case.law/cal/VolumesMetadata.json", "cal"),
            ("https://static.case.law/cal/1/cases/0009-01.json", "cal"),
            ("https://static.case.law/cal/1.tar", "cal"),
            ("https://static.case.law/bur/1/html/0003-01.html", "bur"),
        ],
    )
    def test_routes_to_reporter_or_root(self, url, expected):
        assert shard_key_for_url(url) == expected


class TestShardedManifest:
    def test_get_returns_none_for_unknown_url(self, tmp_path):
        with ShardedManifest(tmp_path) as m:
            assert m.get(CASE_URL) is None

    def test_upsert_then_get_round_trips_all_fields(self, tmp_path):
        entry = make_entry(
            last_modified="Wed, 28 Feb 2024 00:00:00 GMT",
            sha256="f" * 64,
            git_blob_sha1="a" * 40,
            size_bytes=1234,
            first_fetched_at="2026-01-01T00:00:00+00:00",
            last_checked_at="2026-01-02T00:00:00+00:00",
            last_changed_at="2026-01-03T00:00:00+00:00",
        )
        with ShardedManifest(tmp_path) as m:
            m.upsert(entry)
            assert m.get(CASE_URL) == entry

    def test_upsert_is_idempotent_and_updates_in_place(self, tmp_path):
        with ShardedManifest(tmp_path) as m:
            m.upsert(make_entry())
            m.upsert(make_entry(status="checksum_failed", etag='"new"'))
            got = m.get(CASE_URL)
            assert got.status == "checksum_failed"
            assert got.etag == '"new"'
            assert len(m.all_entries()) == 1  # updated, not duplicated

    def test_writes_one_sqlite_file_per_reporter(self, tmp_path):
        with ShardedManifest(tmp_path) as m:
            m.upsert(make_entry(url="https://static.case.law/cal/1/cases/0009-01.json"))
            m.upsert(make_entry(url="https://static.case.law/bur/1/cases/0003-01.json"))
            m.upsert(make_entry(url="https://static.case.law/ReportersMetadata.json"))

        shard_names = {p.stem for p in tmp_path.glob("*.sqlite3")}
        assert shard_names == {"cal", "bur", "_root"}

    def test_state_persists_across_instances(self, tmp_path):
        with ShardedManifest(tmp_path) as m:
            m.upsert(make_entry())
        with ShardedManifest(tmp_path) as m2:
            assert m2.get(CASE_URL) is not None

    def test_all_entries_spans_shards(self, tmp_path):
        with ShardedManifest(tmp_path) as m:
            m.upsert(make_entry(url="https://static.case.law/cal/1/cases/0009-01.json"))
            m.upsert(make_entry(url="https://static.case.law/bur/1/cases/0003-01.json"))
        with ShardedManifest(tmp_path) as m2:
            assert len(m2.all_entries()) == 2


class TestJsonlRoundTrip:
    def test_export_then_import_preserves_entries(self, tmp_path):
        src_dir = tmp_path / "manifest"
        dst_dir = tmp_path / "manifest2"
        jsonl = tmp_path / "manifest.jsonl"

        entries = [
            make_entry(url="https://static.case.law/cal/1/cases/0009-01.json", size_bytes=10),
            make_entry(url="https://static.case.law/bur/1/cases/0003-01.json", size_bytes=20),
        ]
        with ShardedManifest(src_dir) as m:
            for e in entries:
                m.upsert(e)

        assert export_jsonl(src_dir, jsonl) == 2
        assert import_jsonl(jsonl, dst_dir) == 2

        with ShardedManifest(dst_dir) as m2:
            for e in entries:
                assert m2.get(e.url) == e

    def test_export_is_sorted_for_deterministic_diffs(self, tmp_path):
        src_dir = tmp_path / "manifest"
        jsonl = tmp_path / "manifest.jsonl"
        with ShardedManifest(src_dir) as m:
            for url in (
                "https://static.case.law/cal/1/cases/0009-01.json",
                "https://static.case.law/bur/1/cases/0003-01.json",
                "https://static.case.law/ReportersMetadata.json",
            ):
                m.upsert(make_entry(url=url))

        export_jsonl(src_dir, jsonl)
        lines = jsonl.read_text().strip().split("\n")
        import json

        written_urls = [json.loads(line)["url"] for line in lines]
        assert written_urls == sorted(written_urls)


class TestConcurrentAccess:
    """The downloader writes from a ThreadPoolExecutor, so the manifest must
    tolerate concurrent use. Without locking this raises
    sqlite3.InterfaceError: bad parameter or other API misuse."""

    def test_concurrent_upserts_across_shards(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        reporters = ["cal", "bur", "a2d", "_root"]
        urls = [
            f"https://static.case.law/{r}/1/cases/{i:04d}-01.json"
            for r in reporters
            for i in range(40)
        ]

        with ShardedManifest(tmp_path) as m:
            with ThreadPoolExecutor(max_workers=8) as pool:
                errors = [
                    f for f in pool.map(lambda u: _try_upsert(m, u), urls) if f is not None
                ]
            assert errors == []
            assert len(m.all_entries()) == len(urls)

    def test_concurrent_reads_and_writes(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        urls = [f"https://static.case.law/cal/1/cases/{i:04d}-01.json" for i in range(60)]
        with ShardedManifest(tmp_path) as m:
            for u in urls[:30]:
                m.upsert(make_entry(url=u))

            def mixed(u):
                m.get(u)
                return _try_upsert(m, u)

            with ThreadPoolExecutor(max_workers=8) as pool:
                errors = [f for f in pool.map(mixed, urls) if f is not None]
            assert errors == []


def _try_upsert(manifest, url):
    try:
        manifest.upsert(make_entry(url=url))
    except Exception as e:  # noqa: BLE001 - the point is to surface any thread error
        return f"{type(e).__name__}: {e}"
    return None
