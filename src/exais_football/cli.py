"""Local review, verified recovery, and deliberate optional-tool configuration."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from . import __version__
from .config import InstanceLock, Settings


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Exais local football film library")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")
    for name in ("serve", "launch"):
        serve = commands.add_parser(
            name, help="Start local review" + (" and open its browser" if name == "launch" else "")
        )
        serve.add_argument("--data-dir", type=Path)
        serve.add_argument("--port", type=int, default=8765)
        serve.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
        serve.add_argument("--no-browser", action="store_true")
    for name in ("doctor", "backup", "integrity", "cv-check"):
        sub = commands.add_parser(
            name,
            help={
                "doctor": "Report tools without launching or downloading models",
                "backup": "Create a verified full-library archive while the app is stopped",
                "integrity": "Verify the catalog and referenced files",
                "cv-check": "Deliberately probe a configured external CV environment",
            }[name],
        )
        sub.add_argument("--data-dir", type=Path)
    restore = commands.add_parser(
        "restore", help="Validate a full backup; activate only in a new directory"
    )
    restore.add_argument("archive", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--execute", action="store_true")
    restore.add_argument(
        "--expected-sha256", help="Required for activation; use the hash from dry-run"
    )
    inspect = commands.add_parser(
        "inspect-package", help="Verify an archive's manifest and checksums"
    )
    inspect.add_argument("archive", type=Path)
    configure = commands.add_parser(
        "cv-configure",
        help="Validate and store operator-authored CV configuration; never install or download",
    )
    configure.add_argument("file", type=Path)
    configure.add_argument("--data-dir", type=Path)
    configure.add_argument(
        "--replace",
        action="store_true",
        help="Deliberately replace existing configuration, retaining the previous file",
    )
    evaluate = commands.add_parser(
        "evaluate",
        help="Produce a held-out dataset evaluation; never certifies model accuracy automatically",
    )
    evaluate.add_argument("dataset", type=Path)
    evaluate.add_argument("--output", type=Path)
    return root


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def configure_cv(settings, source: Path, overwrite: bool):
    from tempfile import TemporaryDirectory

    from .cv_environment import CONFIG_NAME, configuration
    from .workspace import atomic_bytes, workspace_lock

    if source.stat().st_size > 16384:
        raise ValueError("Configuration is limited to 16 KiB")
    content = source.read_bytes()
    with TemporaryDirectory(prefix="exais-cv-config-") as temporary:
        staged = Path(temporary) / CONFIG_NAME
        staged.write_bytes(content)
        validated = configuration(SimpleNamespace(settings=Settings(Path(temporary))))
        if not validated:
            raise ValueError("No CV configuration found")
    settings.prepare()
    target = settings.data_dir / CONFIG_NAME
    # Operator command only; HTTP requests cannot choose local executable paths.
    with workspace_lock(SimpleNamespace(settings=settings)):
        if target.exists():
            if not overwrite:
                raise ValueError(
                    "Configuration exists; pass --replace to retain and replace it deliberately"
                )
            if target.is_symlink():
                raise ValueError("Configuration may not be a symbolic link")
            from uuid import uuid4

            atomic_bytes(
                target.with_name(f"cv-environment.previous-{uuid4()}.json"), target.read_bytes()
            )
            # Atomic replacement is explicit for this operator configuration only.
            staging = target.with_name(f"cv-environment.new-{uuid4()}.json")
            atomic_bytes(staging, content)
            staging.replace(target)
        else:
            atomic_bytes(target, content)
    return {
        "configured": True,
        "backend": validated["backend"],
        "device": validated["device"],
        "inference_qualified": False,
        "next_command": "exais-football cv-check",
    }


def main(argv: list[str] | None = None) -> int:
    root = parser()
    args = root.parse_args(argv)
    settings = Settings.from_env()
    if getattr(args, "data_dir", None):
        settings = replace(settings, data_dir=args.data_dir)
    try:
        if args.command == "doctor":
            import platform

            from .cv_environment import capability as cv_capability
            from .integrations.kinovea import capability
            from .media import media_capabilities

            emit(
                {
                    "version": __version__,
                    "data_dir": str(settings.data_dir),
                    "platform": platform.system(),
                    "architecture": platform.machine(),
                    "capabilities": {
                        **media_capabilities(),
                        "kinovea": capability(settings.kinovea_executable),
                        "football_cv": cv_capability(SimpleNamespace(settings=settings)),
                    },
                }
            )
        elif args.command == "restore":
            from .recovery import restore_backup

            if args.execute and not args.expected_sha256:
                root.error("Activation requires --expected-sha256 from the dry-run result")
            emit(
                restore_backup(
                    args.archive.absolute(),
                    args.destination.absolute(),
                    dry_run=not args.execute,
                    expected_archive_sha256=args.expected_sha256,
                )
            )
        elif args.command == "inspect-package":
            from .recovery import inspect_package

            emit(inspect_package(args.archive))
        elif args.command == "cv-configure":
            emit(configure_cv(settings, args.file, args.replace))
        elif args.command == "evaluate":
            from .evaluation import evaluate
            from .workspace import atomic_bytes, read_json

            report = evaluate(read_json(args.dataset, limit=16 * 1024**2))
            if args.output:
                atomic_bytes(
                    args.output, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
                )
            emit(report)
        elif args.command in {"backup", "integrity", "cv-check"}:
            from .service import Catalog

            settings.prepare()
            lock = InstanceLock(settings.path("instance.lock"))
            lock.acquire()
            try:
                catalog = Catalog(settings)
                if args.command == "backup":
                    from .recovery import create_backup

                    result = create_backup(catalog)
                    with catalog.db.connection() as db:
                        relative = db.execute(
                            "SELECT path FROM artifact_records WHERE id=?", (result["id"],)
                        ).fetchone()[0]
                    emit({**result, "archive": str(settings.path(relative))})
                elif args.command == "integrity":
                    from .recovery import inspect_workspace

                    emit(inspect_workspace(catalog, verify_hashes=True))
                else:
                    from .cv_environment import probe

                    emit(probe(catalog))
            finally:
                lock.release()
        elif args.command in {"serve", "launch"}:
            if not 1 <= args.port <= 65535:
                root.error("Port must be between 1 and 65535")
            import uvicorn

            from .app import create_app
            from .launcher import open_when_ready

            app = create_app(settings)
            address = f"[{args.host}]" if args.host == "::1" else args.host
            origin = f"http://{address}:{args.port}"
            if args.command == "launch" and not args.no_browser:
                open_when_ready(origin, app.state.instance_id)
            print(f"Open {origin} — press Ctrl+C to stop.")
            uvicorn.run(app, host=args.host, port=args.port, workers=1)
        else:
            root.print_help()
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Exais Football: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        from .media import MediaError
        from .service import CatalogError

        if isinstance(error, (CatalogError, MediaError)):
            print(
                json.dumps({"error": {"code": error.code, "message": error.message}}),
                file=sys.stderr,
            )
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
