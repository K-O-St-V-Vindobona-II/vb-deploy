"""Command line of the vb tool."""

from __future__ import annotations

import argparse
import getpass
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from .commands import cmd_check, cmd_deploy, cmd_setup, cmd_ssh
from .context import Context
from .errors import VbError
from .keycommands import cmd_init, cmd_rotate_key
from .runner import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from types import FrameType

INTERRUPTED = 130
FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    """The argument parser; ansible options after the site name are passed on."""
    parser = argparse.ArgumentParser(
        prog="vb",
        description="Unlock the operator key and run the vb-deploy playbooks.",
    )
    parser.add_argument(
        "--sites-dir",
        type=Path,
        help=(
            "folder with one directory per stage, each holding its inventory, "
            "operator key and secrets (default: ../vb-access/sites next to this "
            "checkout)"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("setup", "set up or verify the server (system, users, SSH, firewall)"),
        ("deploy", "sync secrets and configuration and (re)start the services"),
        ("check", "preview both playbooks without changing the server"),
    ):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("site", help="stage name, e.g. production")
        sub.add_argument("ansible_args", nargs=argparse.REMAINDER)
    ssh = commands.add_parser("ssh", help="open a shell with the operator key")
    ssh.add_argument("site")
    ssh.add_argument("user", nargs="?", default="service", choices=["admin", "service"])
    init = commands.add_parser(
        "init", help="create a stage from the vb-deploy template"
    )
    init.add_argument("site")
    rotate = commands.add_parser("rotate-key", help="replace the operator key")
    rotate.add_argument("site")
    return parser


def _dispatch(ctx: Context, arguments: argparse.Namespace) -> int:
    site: str = arguments.site
    handlers: dict[str, Callable[[], int]] = {
        "setup": lambda: cmd_setup(ctx, site, arguments.ansible_args),
        "deploy": lambda: cmd_deploy(ctx, site, arguments.ansible_args),
        "check": lambda: cmd_check(ctx, site, arguments.ansible_args),
        "ssh": lambda: cmd_ssh(ctx, site, arguments.user),
        "init": lambda: cmd_init(ctx, site),
        "rotate-key": lambda: cmd_rotate_key(ctx, site),
    }
    return handlers[arguments.command]()


@contextmanager
def exit_on_termination() -> Generator[None]:
    """Turn SIGTERM and SIGHUP into a normal exit so cleanup code still runs."""

    def stop(signum: int, _frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)

    previous = {
        number: signal.signal(number, stop)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def default_sites_dir(deploy_dir: Path) -> Path:
    """The sites of the private repository that is checked out next to this one."""
    return deploy_dir.parent / "vb-access" / "sites"


def write_line(text: str) -> None:
    """Print a message for the operator."""
    sys.stdout.write(f"{text}\n")


def main(argv: Sequence[str] | None = None, ctx: Context | None = None) -> int:
    """Run one command and turn failures into a message and an exit code."""
    arguments = build_parser().parse_args(argv)
    deploy_dir = Path(__file__).resolve().parent.parent
    context = ctx or Context(
        sites_dir=(arguments.sites_dir or default_sites_dir(deploy_dir)).resolve(),
        deploy_dir=deploy_dir,
        runner=SubprocessRunner(),
        prompt_secret=getpass.getpass,
        emit=write_line,
    )
    try:
        with exit_on_termination():
            return _dispatch(context, arguments)
    except VbError as error:
        sys.stderr.write(f"vb: {error}\n")
        return FAILED
    except KeyboardInterrupt:
        sys.stderr.write("vb: interrupted\n")
        return INTERRUPTED
