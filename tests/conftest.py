import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def reporters_metadata():
    return load_fixture("ReportersMetadata.json")


@pytest.fixture
def jurisdictions_metadata():
    return load_fixture("JurisdictionsMetadata.json")


@pytest.fixture
def bur_volumes_metadata():
    return load_fixture("bur_VolumesMetadata.json")


@pytest.fixture
def bur_cases_metadata():
    return load_fixture("bur_1_CasesMetadata.json")


@pytest.fixture
def fixture_fetch_json(reporters_metadata, jurisdictions_metadata, bur_volumes_metadata, bur_cases_metadata):
    """A fetch_json callable backed by fixtures — no network access.

    Raises on any URL the tests didn't anticipate, so an accidental live
    fetch fails loudly rather than silently hitting static.case.law.
    """

    def fetch(url: str):
        if url.endswith("/ReportersMetadata.json"):
            return reporters_metadata
        if url.endswith("/JurisdictionsMetadata.json"):
            return jurisdictions_metadata
        if url.endswith("/bur/VolumesMetadata.json"):
            return bur_volumes_metadata
        if url.endswith("/bur/1/CasesMetadata.json"):
            return bur_cases_metadata
        raise AssertionError(f"Unexpected fetch during test: {url}")

    return fetch
