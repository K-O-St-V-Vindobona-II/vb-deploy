"""Probing which login the host accepts, and plain ssh arguments."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .runner import Runner, RunResult
    from .site import Host

CONNECT_TIMEOUT_SECONDS = 10


class LoginOutcome(Enum):
    """What happened when a login with the operator key was tried."""

    OK = "ok"
    DENIED = "denied"
    HOST_KEY_CHANGED = "host key changed"
    UNREACHABLE = "unreachable"


def identity_options(host: Host, key: Path) -> list[str]:
    """Options that make ssh offer only this key and trust a new host on first use."""
    options = [
        "-i",
        str(key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if host.port is not None:
        options += ["-p", str(host.port)]
    return options


def classify(result: RunResult) -> LoginOutcome:
    """Tell a refused login apart from a host that cannot be reached."""
    if result.returncode == 0:
        return LoginOutcome.OK
    if (
        "REMOTE HOST IDENTIFICATION HAS CHANGED" in result.stderr
        or "Host key verification failed" in result.stderr
    ):
        return LoginOutcome.HOST_KEY_CHANGED
    if "Permission denied" in result.stderr:
        return LoginOutcome.DENIED
    return LoginOutcome.UNREACHABLE


def probe_login(runner: Runner, host: Host, user: str, key: Path) -> LoginOutcome:
    """Try a login that uses the public-key method only and runs 'true'."""
    result = runner.capture(
        [
            "ssh",
            *identity_options(host, key),
            "-o",
            "BatchMode=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
            f"{user}@{host.address}",
            "true",
        ]
    )
    return classify(result)


def login_argv(host: Host, user: str, key: Path) -> list[str]:
    """The arguments for an interactive login."""
    return ["ssh", *identity_options(host, key), f"{user}@{host.address}"]
