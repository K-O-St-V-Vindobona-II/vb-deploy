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


STAGE_HELP = "name of the stage, e.g. production"
PASSED_ON_HELP = "go unchanged to ansible-playbook (see examples)"

PLAYBOOK_COMMANDS = {
    "setup": (
        "set up or verify the server (system, users, SSH, firewall)",
        """\
examples:
  ./vb setup production
      first setup of a new server, or check its base configuration again
  ./vb setup production -v
      the same with more output from Ansible

To see beforehand what would change, use ./vb check production.""",
    ),
    "deploy": (
        "sync secrets and configuration and (re)start the services",
        """\
examples:
  ./vb deploy production
      sync secrets, Caddyfile, Quadlets and timers, restart what changed
  ./vb deploy production --tags deploy-api
      only fetch the newest vb-api image now instead of waiting for the
      nightly automatic update, and restart the vb-api pod (API, job worker,
      database and Valkey, on other stages than production also Garage: a
      short interruption); nothing else is synced. The same for the other
      services: --tags deploy-intern, --tags deploy-www
  ./vb deploy production -e vb_api_image_tag=<full-commit-sha>
      run this image version instead of the newest one (roll back, see
      README 2.3); it stays until the next ./vb deploy without -e. Also
      vb_intern_image_tag and vb_www_image_tag
  ./vb deploy production --check --diff
      show what this deploy would change, change nothing""",
    ),
    "check": (
        "preview both playbooks without changing the server",
        """\
examples:
  ./vb check production
      runs setup and deploy with --check --diff and changes nothing

Further options go to both runs, e.g. -v for more output.""",
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """The argument parser; ansible options after the site name are passed on."""
    parser = argparse.ArgumentParser(
        prog="vb",
        description="Unlock the operator key and run the vb-deploy playbooks.",
        epilog="'./vb <command> -h' explains a command and shows examples.",
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
    for name, (summary, examples) in PLAYBOOK_COMMANDS.items():
        sub = commands.add_parser(
            name,
            help=summary,
            description=summary[0].upper() + summary[1:] + ".",
            usage=f"vb {name} [-h] stage [ansible-playbook options ...]",
            epilog=examples,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        sub.add_argument("site", metavar="stage", help=STAGE_HELP)
        sub.add_argument(
            "ansible_args",
            metavar="ansible-playbook options",
            nargs=argparse.REMAINDER,
            help=PASSED_ON_HELP,
        )
    ssh = commands.add_parser(
        "ssh",
        help="open a shell with the operator key",
        description=(
            "Open an interactive shell on the server. It takes no command: "
            "run commands inside the shell."
        ),
    )
    ssh.add_argument("site", metavar="stage", help=STAGE_HELP)
    ssh.add_argument(
        "user",
        nargs="?",
        default="service",
        choices=["admin", "service"],
        help=(
            "log in as service (default, runs the containers) or as admin "
            "(may use sudo, for work on the system)"
        ),
    )
    init = commands.add_parser(
        "init", help="create a stage from the vb-deploy template"
    )
    init.add_argument(
        "site",
        metavar="stage",
        help="name of the new stage; site-template/ needs a folder of that name",
    )
    rotate = commands.add_parser("rotate-key", help="replace the operator key")
    rotate.add_argument(
        "site", metavar="stage", help="stage whose operator key is replaced"
    )
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
