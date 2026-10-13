"""Fail packaging when an install would lose the browser application."""

from pathlib import Path
from zipfile import ZipFile

required = {
    "exais_football/__init__.py",
    "exais_football/app.py",
    "exais_football/cli.py",
    "exais_football/static/index.html",
    "exais_football/static/styles.css",
    "exais_football/static/api.js",
    "exais_football/static/app.js",
    "exais_football/static/review.js",
    "exais_football/static/ui.js",
    "exais_football/static/taxonomy.js",
    "exais_football/static/taxonomy.css",
    "exais_football/static/tag-input.js",
}
wheels = sorted(Path("dist").glob("*.whl"))
if not wheels:
    raise SystemExit("No built wheel found")
for wheel in wheels:
    with ZipFile(wheel) as package:
        names = set(package.namelist())
        missing = required - names
        if missing:
            raise SystemExit(f"Missing package assets: {sorted(missing)}")
        if any(name.endswith((".mp4", ".sqlite3", ".kva")) for name in names):
            raise SystemExit("Runtime media or catalog files entered the wheel")
    print(f"Verified installed assets: {wheel.name}")
