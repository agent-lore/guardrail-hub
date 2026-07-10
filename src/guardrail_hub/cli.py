"""The ``guardrail-hub`` console script: serve, apply, drift, version."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from guardrail_hub.errors import GuardrailHubError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="guardrail-hub", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="serve the dashboard over the registered repos")
    serve.add_argument("--config", type=Path, default=None, help="explicit config path")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    apply_ = sub.add_parser("apply", help="port the canonical kit into a target repo")
    apply_.add_argument("target", type=Path, help="path to the target repo checkout")
    apply_.add_argument("--root-package", default=None, help="override root package detection")
    apply_.add_argument("--with-tool-catalog", action="store_true")
    apply_.add_argument("--with-containers", action="store_true")

    drift = sub.add_parser("drift", help="kit drift report across the registered repos")
    drift.add_argument("--config", type=Path, default=None, help="explicit config path")
    drift.add_argument("--repo", default=None, help="limit to one registered repo")
    drift.add_argument("--format", choices=("text", "json"), default="text")

    sub.add_parser("version", help="print hub and kit versions")
    return parser


def _cmd_drift(args: argparse.Namespace) -> int:
    from guardrail_hub.config import load_config
    from guardrail_hub.drift import compare_repo

    config = load_config(args.config)
    entries = [e for e in config.repos if args.repo is None or e.name == args.repo]
    if args.repo is not None and not entries:
        raise GuardrailHubError(f"repo {args.repo!r} is not in the registry")

    reports = [compare_repo(entry) for entry in entries]
    dirty = any(f.status in ("differs", "missing", "error") for r in reports for f in r.files)

    if args.format == "json":
        payload = [
            {
                "repo": r.repo,
                "kit_version": r.kit_version,
                "installed_version": r.installed_version,
                "files": [
                    {"path": f.path, "role": f.role, "status": f.status, "detail": f.detail}
                    for f in r.files
                ],
            }
            for r in reports
        ]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        for report in reports:
            noteworthy = [f for f in report.files if f.status != "same"]
            summary = "clean" if not noteworthy else f"{len(noteworthy)} finding(s)"
            sys.stdout.write(
                f"{report.repo}: kit {report.installed_version} "
                f"(canonical {report.kit_version}) — {summary}\n"
            )
            for f in noteworthy:
                detail = f" — {f.detail}" if f.detail else ""
                sys.stdout.write(f"  {f.status:<8} {f.path} [{f.role}]{detail}\n")
    return 1 if dirty else 0


def _cmd_version() -> int:
    from guardrail_hub import __version__
    from guardrail_hub.kit import kit_version

    sys.stdout.write(f"guardrail-hub {__version__} (kit {kit_version()})\n")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from guardrail_hub.web import serve

    return serve(config_path=args.config, host=args.host, port=args.port)


def _cmd_apply(args: argparse.Namespace) -> int:
    from guardrail_hub.installer import apply_kit

    report = apply_kit(
        args.target.expanduser().resolve(),
        root_package=args.root_package,
        with_tool_catalog=args.with_tool_catalog,
        with_containers=args.with_containers,
    )
    sys.stdout.write(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "drift":
            return _cmd_drift(args)
        if args.command == "version":
            return _cmd_version()
        if args.command == "serve":
            return _cmd_serve(args)
        if args.command == "apply":
            return _cmd_apply(args)
    except GuardrailHubError as exc:
        sys.stderr.write(f"guardrail-hub: {exc}\n")
        return 1
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
