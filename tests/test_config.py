import pytest

from hls_caselaw_downloader.config import ConfigError, load_config

VALID = """
version: 1
output_dir: ./mirror
formats: [json]
selections:
  - reporter: cal
    volumes: "1-5"
"""


def write(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text)
    return path


class TestLoadConfig:
    def test_valid_config(self, tmp_path):
        config = load_config(write(tmp_path, VALID))
        assert config.output_dir == "./mirror"
        assert len(config.selections) == 1
        assert config.selections[0].reporter == "cal"

    def test_defaults_are_applied(self, tmp_path):
        config = load_config(write(tmp_path, "version: 1\n"))
        assert config.concurrency.max_workers == 4
        assert config.concurrency.requests_per_second == 5.0
        assert config.http.max_retries == 5
        assert config.manifest.dir == "./manifest"

    def test_per_selection_formats_override_top_level(self, tmp_path):
        text = """
version: 1
formats: [json]
selections:
  - reporter: cal
  - reporter: bur
    formats: [case_pdf, tar]
"""
        config = load_config(write(tmp_path, text))
        assert config.selections[0].formats == ["json"]
        assert config.selections[1].formats == ["case_pdf", "tar"]

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nope.yaml")

    def test_wrong_version_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="version"):
            load_config(write(tmp_path, "version: 99\n"))

    def test_invalid_yaml_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, "version: 1\n  bad: [indent\n"))

    def test_unknown_top_level_format_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="Unknown format"):
            load_config(write(tmp_path, "version: 1\nformats: [xml]\n"))

    def test_unknown_selection_format_raises(self, tmp_path):
        text = """
version: 1
selections:
  - reporter: cal
    formats: [xml]
"""
        with pytest.raises(ConfigError, match="Unknown format"):
            load_config(write(tmp_path, text))

    def test_selection_without_target_raises(self, tmp_path):
        text = """
version: 1
selections:
  - volumes: "1-5"
"""
        with pytest.raises(ConfigError, match=r"selections\[0\]"):
            load_config(write(tmp_path, text))

    def test_selections_must_be_a_list(self, tmp_path):
        with pytest.raises(ConfigError, match="must be a list"):
            load_config(write(tmp_path, "version: 1\nselections: {reporter: cal}\n"))

    def test_unknown_section_key_raises_a_helpful_error(self, tmp_path):
        """A typo (or a key removed in a later version) must not surface as
        a raw TypeError traceback."""
        with pytest.raises(ConfigError, match="Unknown key"):
            load_config(write(tmp_path, "version: 1\nmanifest:\n  frmat: sqlite\n"))

    def test_error_names_the_offending_key_and_valid_keys(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_config(write(tmp_path, "version: 1\nhttp:\n  timeout: 5\n"))
        assert "timeout" in str(exc.value)
        assert "timeout_seconds" in str(exc.value)

    @pytest.mark.parametrize("section", ["concurrency", "http", "manifest"])
    def test_section_must_be_a_mapping(self, tmp_path, section):
        with pytest.raises(ConfigError, match="must be a mapping"):
            load_config(write(tmp_path, f"version: 1\n{section}: [1, 2]\n"))


class TestShippedConfigs:
    """The configs in config/ are user-facing documentation — they must parse."""

    @pytest.mark.parametrize("name", ["example.yaml", "minimal.yaml", "smoke-test.yaml"])
    def test_shipped_config_is_valid(self, name):
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "config" / name
        config = load_config(config_path)
        assert config.selections, f"{name} should define at least one selection"
