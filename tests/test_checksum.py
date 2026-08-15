import subprocess

import pytest

from hls_caselaw_downloader.checksum import (
    ChecksumMismatch,
    git_blob_sha1,
    parse_sha256sum_file,
    sha256_hex,
    verify_sha256,
)

# Well-known git blob hashes — these are what `git hash-object` produces.
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
HELLO_BLOB = "ce013625030ba8dba906f756967f9e9ca394464a"


class TestGitBlobSha1:
    def test_known_hashes(self):
        assert git_blob_sha1(b"") == EMPTY_BLOB
        assert git_blob_sha1(b"hello\n") == HELLO_BLOB

    def test_matches_real_git_hash_object(self, tmp_path):
        """The whole point of using git's formula is interoperability, so
        assert against the real thing rather than only our own constants."""
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        for payload in (b"", b"hello\n", b'{"case": "data"}'):
            result = subprocess.run(
                ["git", "hash-object", "--stdin"],
                cwd=tmp_path,
                input=payload,
                capture_output=True,
                check=True,
            )
            assert git_blob_sha1(payload) == result.stdout.decode().strip()

    def test_is_content_addressed(self):
        # Same content -> same hash, regardless of when/where it was computed.
        assert git_blob_sha1(b"abc") == git_blob_sha1(b"abc")
        assert git_blob_sha1(b"abc") != git_blob_sha1(b"abd")


class TestParseSha256SumFile:
    def test_standard_sha256sum_format(self):
        digest = "a" * 64
        assert parse_sha256sum_file(f"{digest}  cal/1.tar\n".encode()) == digest

    def test_bare_digest(self):
        digest = "b" * 64
        assert parse_sha256sum_file(digest.encode()) == digest

    def test_uppercase_is_normalized(self):
        assert parse_sha256sum_file((("A" * 64) + "  x.tar").encode()) == "a" * 64

    @pytest.mark.parametrize("content", [b"", b"   ", b"nothex  file.tar", b"abc123  file.tar"])
    def test_unparseable_raises(self, content):
        with pytest.raises(ValueError):
            parse_sha256sum_file(content)


class TestVerifySha256:
    def test_matching_digest_passes(self):
        data = b"some tar bytes"
        verify_sha256(data, sha256_hex(data))  # must not raise

    def test_case_insensitive(self):
        data = b"some tar bytes"
        verify_sha256(data, sha256_hex(data).upper())

    def test_mismatch_raises(self):
        with pytest.raises(ChecksumMismatch):
            verify_sha256(b"abc", "0" * 64)
