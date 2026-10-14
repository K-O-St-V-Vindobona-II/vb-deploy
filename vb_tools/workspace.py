"""A short-lived private directory for the decrypted key and the vault password."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import TracebackType

SHARED_MEMORY = Path("/dev/shm")  # noqa: S108 - RAM-backed, never written to disk


def secure_base() -> Path:
    """Prefer RAM-backed storage so nothing secret reaches the disk."""
    if SHARED_MEMORY.is_dir() and os.access(SHARED_MEMORY, os.W_OK):
        return SHARED_MEMORY
    return Path(tempfile.gettempdir())


class Workspace:
    """A directory only the current user can enter, removed on exit."""

    def __init__(self, base: Path | None = None) -> None:
        """Create the directory (mode 0700) below the given or the secure base."""
        self.path = Path(tempfile.mkdtemp(prefix="vb-", dir=base or secure_base()))

    def write_secret(self, name: str, data: bytes) -> Path:
        """Write a new file readable by the current user only and return its path."""
        if Path(name).name != name:
            message = f"A secret file name must not contain a path: {name}"
            raise ValueError(message)
        target = self.path / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return target

    def close(self) -> None:
        """Remove the directory and everything in it."""
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> Workspace:
        """Use the workspace in a with block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Remove the directory, also when the block failed."""
        self.close()
