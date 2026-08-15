import json
import logging

from hls_caselaw_downloader.logging_setup import configure_logging


def _emit(caplog_stream, message="hello", level=logging.INFO, **kwargs):
    logging.getLogger("hls_caselaw_downloader.test").log(level, message, **kwargs)
    return caplog_stream.getvalue()


class TestConfigureLogging:
    def test_plain_format_is_human_readable(self, capsys):
        configure_logging()
        logging.getLogger("hls_caselaw_downloader.test").info("syncing %d files", 5)
        err = capsys.readouterr().err
        assert "syncing 5 files" in err
        assert "INFO" in err

    def test_json_logs_emit_parseable_lines(self, capsys):
        """--json-logs exists so a future CI job can parse run summaries."""
        configure_logging(json_logs=True)
        logging.getLogger("hls_caselaw_downloader.test").warning("failed to fetch %s", "x.json")
        line = capsys.readouterr().err.strip()
        payload = json.loads(line)
        assert payload["level"] == "WARNING"
        assert payload["message"] == "failed to fetch x.json"
        assert payload["logger"] == "hls_caselaw_downloader.test"
        assert "time" in payload

    def test_json_logs_include_exception_info(self, capsys):
        configure_logging(json_logs=True)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("hls_caselaw_downloader.test").exception("unexpected")
        payload = json.loads(capsys.readouterr().err.strip())
        assert "ValueError: boom" in payload["exc_info"]

    def test_verbose_enables_debug(self, capsys):
        configure_logging(verbose=True)
        logging.getLogger("hls_caselaw_downloader.test").debug("per-file detail")
        assert "per-file detail" in capsys.readouterr().err

    def test_debug_suppressed_by_default(self, capsys):
        configure_logging()
        logging.getLogger("hls_caselaw_downloader.test").debug("per-file detail")
        assert "per-file detail" not in capsys.readouterr().err

    def test_repeated_configuration_does_not_duplicate_output(self, capsys):
        configure_logging()
        configure_logging()
        logging.getLogger("hls_caselaw_downloader.test").info("once")
        assert capsys.readouterr().err.count("once") == 1
