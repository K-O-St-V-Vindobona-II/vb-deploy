"""Running external programs, behind an interface the tests can replace."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .errors import VbError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

CAPTURE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class RunResult:
    """Outcome of a captured program run."""

    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    """How the commands start programs."""

    def capture(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult:
        """Run a program and return its output; nothing reaches the terminal."""
        ...

    def interactive(self, argv: Sequence[str], cwd: Path | None = None) -> int:
        """Run a program on the terminal (prompts, progress); return its exit code."""
        ...


class SubprocessRunner:
    """The real runner, based on subprocess."""

    def capture(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult:
        """Run a program with captured output and a time limit."""
        try:
            done = subprocess.run(  # noqa: S603 - argv is built from fixed program names
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=CAPTURE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as error:
            raise _missing_program(argv) from error
        except subprocess.TimeoutExpired as error:
            message = (
                f"{argv[0]} did not finish within {CAPTURE_TIMEOUT_SECONDS} seconds."
            )
            raise VbError(message) from error
        return RunResult(done.returncode, done.stdout, done.stderr)

    def interactive(self, argv: Sequence[str], cwd: Path | None = None) -> int:
        """Run a program with the terminal attached."""
        try:
            return subprocess.run(  # noqa: S603 - argv is built from fixed program names
                list(argv), cwd=cwd, check=False
            ).returncode
        except FileNotFoundError as error:
            raise _missing_program(argv) from error


def _missing_program(argv: Sequence[str]) -> VbError:
    return VbError(f"The program '{argv[0]}' is not installed.")
