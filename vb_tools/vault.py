"""Reading and writing Ansible Vault files through ansible-vault."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import VbError

if TYPE_CHECKING:
    from pathlib import Path

    from .runner import Runner


def decrypt_to_text(runner: Runner, password_file: Path, path: Path) -> str:
    """Return the plain text of a vault file; nothing is written to disk."""
    result = runner.capture(
        [
            "ansible-vault",
            "decrypt",
            "--vault-password-file",
            str(password_file),
            "--output",
            "-",
            str(path),
        ]
    )
    if result.returncode != 0:
        message = f"Cannot decrypt {path.name}: wrong vault password or damaged file."
        raise VbError(message)
    return result.stdout


def encrypt_file(
    runner: Runner, password_file: Path, source: Path, destination: Path
) -> None:
    """Encrypt a plain file into destination; the source stays untouched."""
    result = runner.capture(
        [
            "ansible-vault",
            "encrypt",
            "--vault-password-file",
            str(password_file),
            "--output",
            str(destination),
            str(source),
        ]
    )
    if result.returncode != 0:
        message = f"Cannot encrypt {destination.name}: {result.stderr.strip()}"
        raise VbError(message)
