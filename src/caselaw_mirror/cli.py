"""argparse-based CLI: sync, plan, verify, status, list-reporters.

No interactive prompts anywhere — every failure mode goes to stderr with a
defined exit code (see exit_codes.py), so a future CI job can branch on the
exit code alone.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from caselaw_mirror import exit_codes
from caselaw_mirror.config import Config, ConfigError, load_config
from caselaw_mirror.downloader import sync_files, verify_local_files
from caselaw_mirror.http_client import HttpClient
from caselaw_mirror.logging_setup import configure_logging
from caselaw_mirror.manifest import ShardedManifest, export_jsonl, import_jsonl
from caselaw_mirror.planner import RemoteFile, plan_all
from caselaw_mirror.remote_index import RemoteIndex
from caselaw_mirror.selection import Selection, SelectionError, resolve_selections

logger = logging.getLogger("caselaw_mirror.cli")


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to a YAML config file")
    parser.add_argument("--output-dir", help="Local mirror root (overrides config output_dir)")
    parser.add_argument("--manifest-dir", help="Manifest directory (overrides config manifest.dir)")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--json-logs", action="store_true", help="Emit structured JSON log lines")


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reporter", help="Reporter slug/id, or 'all'")
    parser.add_argument("--jurisdiction", help="Jurisdiction code, e.g. 'Cal.'")
    parser.add_argument("--volumes", help='Volume range, e.g. "1-50", "1,3,5-9", or "all"')
    parser.add_argument("--years", help='Year range, e.g. "1850-1900"')
    parser.add_argument("--cases", help="Comma-separated exact case-file stems, e.g. 0009-01,0015-01")
    parser.add_argument("--formats", help="Comma-separated formats, e.g. json,case_pdf")
    parser.add_argument(
        "--only",
        action="store_true",
        help="Use only the CLI-built selection, ignoring selections in --config",
    )


def build_cli_selection(args: argparse.Namespace) -> Selection | None:
    if not (getattr(args, "reporter", None) or getattr(args, "jurisdiction", None)):
        return None
    kwargs: dict[str, object] = {}
    if args.reporter:
        kwargs["reporter"] = args.reporter
    if args.jurisdiction:
        kwargs["jurisdiction"] = args.jurisdiction
    if args.volumes:
        kwargs["volumes"] = args.volumes
    if args.years:
        kwargs["years"] = args.years
    if args.cases:
        kwargs["cases"] = [c.strip() for c in args.cases.split(",") if c.strip()]
    if args.formats:
        kwargs["formats"] = [f.strip() for f in args.formats.split(",") if f.strip()]
    return Selection(**kwargs)


def _load_config_or_default(args: argparse.Namespace) -> Config:
    """Load config (or an all-defaults one), then apply CLI path overrides.

    The path overrides exist so a single config file works unchanged both
    inside the container (where paths are the /data mount points) and on a
    dev machine, without maintaining two near-identical config files.
    """
    config_path = getattr(args, "config", None)
    config = Config(version=1) if config_path is None else load_config(config_path)

    if getattr(args, "output_dir", None):
        config.output_dir = args.output_dir
    if getattr(args, "manifest_dir", None):
        config.manifest.dir = args.manifest_dir
    return config


def _build_plan(config: Config, args: argparse.Namespace) -> tuple[list[RemoteFile], HttpClient]:
    cli_selection = build_cli_selection(args)
    selections = resolve_selections(config.selections, cli_selection, only=args.only)
    if not selections:
        raise SelectionError(
            "No selections resolved: provide 'selections:' in --config, or --reporter/--jurisdiction"
        )
    client = HttpClient(config.http, requests_per_second=config.concurrency.requests_per_second)
    index = RemoteIndex(client.get_json)
    files = plan_all(selections, index, Path(config.output_dir))
    return files, client


def cmd_plan(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    files, client = _build_plan(config, args)
    client.close()

    by_format: dict[str, int] = {}
    for f in files:
        by_format[f.format] = by_format.get(f.format, 0) + 1

    for f in files:
        print(f"{f.format:12s} {f.url}")
    print(f"\n{len(files)} files planned", file=sys.stderr)
    for fmt, count in sorted(by_format.items()):
        print(f"  {fmt}: {count}", file=sys.stderr)
    return exit_codes.OK


def cmd_sync(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    files, client = _build_plan(config, args)

    manifest = ShardedManifest(config.manifest.dir)
    try:
        summary = sync_files(
            client,
            manifest,
            files,
            max_workers=config.concurrency.max_workers,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    finally:
        manifest.close()
        client.close()

    return exit_codes.OK if summary.ok else exit_codes.PARTIAL_FAILURE


def cmd_verify(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    manifest = ShardedManifest(config.manifest.dir)
    try:
        entries = manifest.all_entries()
    finally:
        manifest.close()

    result = verify_local_files(entries)
    logger.info(
        "verify: %d checked, %d ok, %d mismatched, %d missing",
        result.checked,
        result.ok,
        len(result.mismatched),
        len(result.missing),
    )
    for url in result.mismatched:
        print(f"MISMATCH  {url}")
    for url in result.missing:
        print(f"MISSING   {url}")

    return exit_codes.OK if not result.mismatched and not result.missing else exit_codes.PARTIAL_FAILURE


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    manifest_dir = config.manifest.dir
    manifest = ShardedManifest(manifest_dir)
    try:
        entries = manifest.all_entries()
    finally:
        manifest.close()

    if not entries:
        print(f"No manifest entries found under {manifest_dir}")
        return exit_codes.OK

    by_status: dict[str, int] = {}
    total_bytes = 0
    last_changed = None
    for e in entries:
        by_status[e.status] = by_status.get(e.status, 0) + 1
        total_bytes += e.size_bytes or 0
        if e.last_changed_at and (last_changed is None or e.last_changed_at > last_changed):
            last_changed = e.last_changed_at

    print(f"{len(entries)} files tracked in {manifest_dir}")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")
    print(f"total bytes: {total_bytes}")
    print(f"most recent change: {last_changed or 'never'}")
    return exit_codes.OK


def cmd_manifest(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    if args.manifest_command == "export":
        count = export_jsonl(config.manifest.dir, args.to)
        logger.info("exported %d manifest entries to %s", count, args.to)
    else:
        count = import_jsonl(getattr(args, "from"), config.manifest.dir)
        logger.info("imported %d manifest entries into %s", count, config.manifest.dir)
    return exit_codes.OK


def cmd_list_reporters(args: argparse.Namespace) -> int:
    config = _load_config_or_default(args)
    client = HttpClient(config.http, requests_per_second=config.concurrency.requests_per_second)
    try:
        index = RemoteIndex(client.get_json)
        reporters = index.get_reporters()
    finally:
        client.close()

    for r in sorted(reporters, key=lambda r: r.slug):
        print(f"{r.slug:20s} {r.start_year}-{r.end_year}  {r.full_name}")
    print(f"\n{len(reporters)} reporters", file=sys.stderr)
    return exit_codes.OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="caselaw-mirror", description="Mirror CAP bulk data from static.case.law")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_sync = subparsers.add_parser("sync", help="Download planned files, skipping unchanged ones")
    _add_common_args(p_sync)
    _add_selection_args(p_sync)
    p_sync.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing")
    p_sync.add_argument("--limit", type=int, default=None, help="Cap the number of files processed this run")
    p_sync.set_defaults(func=cmd_sync)

    p_plan = subparsers.add_parser("plan", help="Resolve selections and print the file list; no downloads")
    _add_common_args(p_plan)
    _add_selection_args(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_verify = subparsers.add_parser("verify", help="Re-checksum local files against the manifest; no network")
    _add_common_args(p_verify)
    p_verify.set_defaults(func=cmd_verify)

    p_status = subparsers.add_parser("status", help="Summarize manifest state")
    _add_common_args(p_status)
    p_status.set_defaults(func=cmd_status)

    p_manifest = subparsers.add_parser("manifest", help="Convert the manifest to/from JSONL")
    manifest_sub = p_manifest.add_subparsers(dest="manifest_command", required=True)

    p_export = manifest_sub.add_parser("export", help="Write a sorted, diffable JSONL snapshot")
    _add_common_args(p_export)
    p_export.add_argument("--to", required=True, help="Output .jsonl path")
    p_export.set_defaults(func=cmd_manifest)

    p_import = manifest_sub.add_parser("import", help="Load a JSONL snapshot into the SQLite shards")
    _add_common_args(p_import)
    p_import.add_argument("--from", required=True, dest="from", help="Input .jsonl path")
    p_import.set_defaults(func=cmd_manifest)

    p_list = subparsers.add_parser("list-reporters", help="Fetch and print ReportersMetadata.json")
    _add_common_args(p_list)
    p_list.set_defaults(func=cmd_list_reporters)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, json_logs=args.json_logs)

    try:
        return args.func(args)
    except (ConfigError, SelectionError) as e:
        logger.error("%s", e)
        return exit_codes.CONFIG_ERROR
    except Exception:
        logger.exception("unexpected error")
        return exit_codes.FATAL


if __name__ == "__main__":
    sys.exit(main())
