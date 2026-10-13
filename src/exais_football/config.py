"""Explicit local configuration; request data never selects filesystem roots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class InstanceLock:
    """An OS-released lock prevents two workers from owning one local catalog."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self) -> None:
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError(
                "Another Exais process is using this data directory; "
                "stop it or choose another directory"
            ) from error
        self.stream = stream

    def release(self) -> None:
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 2 * 1024**3
    kinovea_executable: str | None = None
    football_cv_python: str | None = None
    max_pending_jobs: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", self.data_dir.expanduser().resolve())
        if self.max_upload_bytes < 1 or self.max_pending_jobs < 1:
            raise ValueError("Upload and job limits must be positive")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.environ.get("EXAIS_FOOTBALL_DATA", "~/.exais-football")),
            max_upload_bytes=int(os.environ.get("EXAIS_FOOTBALL_MAX_UPLOAD_BYTES", 2 * 1024**3)),
            kinovea_executable=os.environ.get("EXAIS_KINOVEA_EXE") or None,
            football_cv_python=os.environ.get("EXAIS_FOOTBALL_CV_PYTHON") or None,
        )

    def prepare(self) -> None:
        if (self.data_dir / ".restore-in-progress").exists():
            raise RuntimeError(
                "This workspace contains an interrupted restore "
                "and has not been activated. Restore the verified "
                "archive to a different new directory."
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "sources",
            "staging",
            "thumbnails",
            "exports",
            "reviews",
            "artifacts",
            "cache",
            "analysis",
            "backups",
            "packages",
        ):
            self.path(name).mkdir(exist_ok=True)

    def path(self, relative: str) -> Path:
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("Path escapes the configured data directory")
        return path
