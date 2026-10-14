"""Finding out which login the host accepts right now."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from .errors import VbError
from .keys import public_key_of, same_key
from .site import read_operator_keys
from .ssh import LoginOutcome, probe_login

if TYPE_CHECKING:
    from pathlib import Path

    from .context import Context
    from .site import Host, Site


class AccessMode(Enum):
    """How the next setup run has to log in."""

    ADMIN_KEY = "an already set up host: admin with the operator key"
    ROOT_KEY = "a new host that already knows the operator key: root with the key"
    ROOT_PROVIDER_LOGIN = "a new host: root with the password from the hosting provider"

    @property
    def user(self) -> str:
        """The account the playbook connects as."""
        return "admin" if self is AccessMode.ADMIN_KEY else "root"


def _failure(outcome: LoginOutcome, host: Host) -> VbError:
    if outcome is LoginOutcome.HOST_KEY_CHANGED:
        message = (
            f"The host key of {host.address} differs from the one on record. If the "
            f"server was rebuilt on purpose, remove the old entry with: "
            f"ssh-keygen -R {host.address}"
        )
        return VbError(message)
    message = (
        f"Cannot reach {host.address}: check the address in the inventory, the "
        f"network and that the server is running."
    )
    return VbError(message)


def _accepts(ctx: Context, host: Host, user: str, key: Path) -> bool:
    outcome = probe_login(ctx.runner, host, user, key)
    if outcome is LoginOutcome.OK:
        return True
    if outcome is LoginOutcome.DENIED:
        return False
    raise _failure(outcome, host)


def detect_access_mode(ctx: Context, host: Host, key: Path) -> AccessMode:
    """Try admin, then root, with the key; otherwise a root password is needed."""
    if _accepts(ctx, host, "admin", key):
        return AccessMode.ADMIN_KEY
    if _accepts(ctx, host, "root", key):
        return AccessMode.ROOT_KEY
    return AccessMode.ROOT_PROVIDER_LOGIN


def require_login(ctx: Context, host: Host, user: str, key: Path) -> None:
    """Stop with a hint if the operator key does not log in as this user."""
    if _accepts(ctx, host, user, key):
        return
    message = (
        f"{host.address} does not accept the operator key for '{user}'. On a new "
        f"host run './vb setup' first."
    )
    raise VbError(message)


def require_key_listed(ctx: Context, site: Site, key: Path) -> None:
    """Stop if setup would remove the key it connects with.

    setup_vps.yml leaves exactly the listed keys on the host. With the operator
    key missing from the list, the run would delete its own access and, once
    root is closed, lock everybody out.
    """
    public_key = public_key_of(ctx.runner, key)
    listed = read_operator_keys(site.inventory.read_text(encoding="utf-8"))
    if any(same_key(entry, public_key) for entry in listed):
        return
    message = (
        "The operator key is not listed in operator_public_keys of "
        f"{site.inventory.name}. Running setup would remove it from the server "
        "and lock you out."
    )
    raise VbError(message)
