"""The operator key pair: validate, generate, compare."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import VbError

if TYPE_CHECKING:
    from pathlib import Path

    from .runner import Runner

PRIVATE_KEY_HEADER = "-----BEGIN OPENSSH PRIVATE KEY-----"
PRIVATE_KEY_FOOTER = "-----END OPENSSH PRIVATE KEY-----"


def normalize_private_key(text: str) -> str:
    """Check that the text is an OpenSSH private key and end it with a newline.

    ssh refuses a key file that lacks the final newline.
    """
    stripped = text.strip()
    if not (
        stripped.startswith(PRIVATE_KEY_HEADER)
        and stripped.endswith(PRIVATE_KEY_FOOTER)
    ):
        message = "The operator key file does not contain an OpenSSH private key."
        raise VbError(message)
    return stripped + "\n"


def key_identity(public_key_line: str) -> tuple[str, str]:
    """The type and the base64 body of a public key, ignoring the comment."""
    fields = public_key_line.split()
    if len(fields) < 2:
        message = f"Not a public key line: {public_key_line!r}"
        raise VbError(message)
    return fields[0], fields[1]


def same_key(first: str, second: str) -> bool:
    """Whether two public key lines are the same key (comments may differ)."""
    return key_identity(first) == key_identity(second)


def public_key_of(runner: Runner, private_key: Path) -> str:
    """Derive the public key line from a private key file."""
    result = runner.capture(["ssh-keygen", "-y", "-f", str(private_key)])
    if result.returncode != 0:
        message = "ssh-keygen could not read the operator key."
        raise VbError(message)
    return result.stdout.strip()


def generate_key(runner: Runner, directory: Path, label: str) -> tuple[Path, str]:
    """Create an ed25519 key pair without a passphrase and return both parts."""
    private_key = directory / "generated_key"
    result = runner.capture(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            label,
            "-f",
            str(private_key),
        ]
    )
    if result.returncode != 0:
        message = f"ssh-keygen failed: {result.stderr.strip()}"
        raise VbError(message)
    public_key = (directory / "generated_key.pub").read_text(encoding="utf-8").strip()
    return private_key, public_key
