"""Console entry point. The application deliberately binds to loopback only."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import Settings


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Exais local football film library")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Start the local review application")
    serve.add_argument("--data-dir", type=Path, help="Catalog and original-media directory")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    commands.add_parser("doctor", help="Report local tools without launching or downloading them")
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    settings = Settings.from_env()
    if arguments.command == "doctor":
        from .integrations.kinovea import capability
        from .media import media_capabilities

        print(
            json.dumps(
                {
                    "version": __version__,
                    "data_dir": str(settings.data_dir),
                    "capabilities": {
                        **media_capabilities(),
                        "kinovea": capability(settings.kinovea_executable),
                    },
                    "football_cv": "External and optional; no model download or inference",
                },
                indent=2,
            )
        )
        return 0
    if arguments.command != "serve":
        parser().print_help()
        return 0
    if not 1 <= arguments.port <= 65535:
        parser().error("Port must be between 1 and 65535")
    if arguments.data_dir:
        settings = replace(settings, data_dir=arguments.data_dir)
    import uvicorn

    from .app import create_app

    address = f"[{arguments.host}]" if arguments.host == "::1" else arguments.host
    print(f"Open http://{address}:{arguments.port} — press Ctrl+C to stop.")
    uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
