"""Integrity verification.

CAP only publishes a real checksum for volume-level tar bundles (the
sibling `<volume>.tar.sha256` file, in standard `sha256sum` output format).
Everywhere else (individual case JSON/HTML/PDF, metadata files), no
published checksum exists — ETag/Last-Modified from http_client is the only
change signal there.

For local integrity tracking regardless of format, we compute git's blob
SHA-1 (`sha1("blob " + len + "\0" + content)`). It's a pure function of
content — unaffected by history rewrites (rebase/squash/amend all change
commit SHAs, never blob SHAs) — so it's a stable identity even though this
project doesn't commit mirrored data to git.
"""

from __future__ import annotations

import hashlib


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ChecksumMismatch(ValueError):
    """Raised when downloaded content doesn't match a published checksum."""


def parse_sha256sum_file(content: bytes) -> str:
    """Parse a `sha256sum`-format file (`<hex>  <filename>`) and return the hex digest.

    Also tolerates a bare hex digest with no filename, in case the format
    changes upstream.
    """
    text = content.decode("ascii", errors="strict").strip()
    if not text:
        raise ValueError("Empty .tar.sha256 content")
    first_token = text.split()[0].lower()
    if len(first_token) != 64 or any(c not in "0123456789abcdef" for c in first_token):
        raise ValueError(f"Could not parse a sha256 hex digest from: {text!r}")
    return first_token


def verify_sha256(data: bytes, expected_hex: str) -> None:
    actual = sha256_hex(data)
    if actual.lower() != expected_hex.lower():
        raise ChecksumMismatch(f"sha256 mismatch: expected {expected_hex}, got {actual}")
