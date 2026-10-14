"""What every command needs: paths, the runner, prompts, and the unlocked key."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import VbError
from .keys import normalize_private_key
from .site import Host, parse_single_host
from .vault import decrypt_to_text
from .workspace import Workspace

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from pathlib import Path

    from .runner import Runner
    from .site import Site

INSTALL_HINTS = {
    "ansible-playbook": "Install Ansible (see the README of vb-deploy).",
    "ansible-inventory": "Install Ansible (see the README of vb-deploy).",
    "ansible-vault": "Install Ansible (see the README of vb-deploy).",
    "sshpass": "Install the package 'sshpass'; it passes the provider's root password.",
    "ssh": "Install the OpenSSH client.",
    "ssh-keygen": "Install the OpenSSH client.",
}


@dataclass
class Context:
    """The collaborators of the commands; tests replace the runner and prompts."""

    sites_dir: Path
    deploy_dir: Path
    runner: Runner
    prompt_secret: Callable[[str], str]
    emit: Callable[[str], None]
    which: Callable[[str], str | None] = shutil.which
    workspace_base: Path | None = None


@dataclass(frozen=True)
class Unlocked:
    """The decrypted operator key and the vault password file, valid in a block."""

    workspace: Workspace
    key: Path
    password_file: Path


def require_tools(ctx: Context, names: Sequence[str]) -> None:
    """Stop with an install hint if a needed program is missing."""
    missing = [name for name in names if ctx.which(name) is None]
    if not missing:
        return
    hints = "\n".join(f"  {name}: {INSTALL_HINTS[name]}" for name in missing)
    message = f"Missing programs:\n{hints}"
    raise VbError(message)


def load_host(ctx: Context, site: Site) -> Host:
    """Read the site's single host from its inventory."""
    result = ctx.runner.capture(
        ["ansible-inventory", "-i", str(site.inventory), "--list"],
        cwd=ctx.deploy_dir,
    )
    if result.returncode != 0:
        message = (
            f"ansible-inventory could not read the inventory: {result.stderr.strip()}"
        )
        raise VbError(message)
    return parse_single_host(result.stdout)


@contextmanager
def unlocked(ctx: Context, site: Site) -> Generator[Unlocked]:
    """Ask for the vault password once and unpack the operator key into RAM."""
    with Workspace(ctx.workspace_base) as workspace:
        password = ctx.prompt_secret("Vault password: ")
        if not password:
            message = "The vault password must not be empty."
            raise VbError(message)
        password_file = workspace.write_secret("vault_pass", f"{password}\n".encode())
        key_text = decrypt_to_text(ctx.runner, password_file, site.key_vault)
        key = workspace.write_secret(
            "operator_key", normalize_private_key(key_text).encode()
        )
        yield Unlocked(workspace=workspace, key=key, password_file=password_file)
