"""The commands that create or replace the operator key: init and rotate-key."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .access import require_login
from .commands import ANSIBLE_TOOLS
from .context import load_host, require_tools, unlocked
from .errors import VbError
from .keys import generate_key, public_key_of, same_key
from .playbooks import setup_argv
from .site import SITE_NAME, load_site, read_operator_keys, with_operator_keys
from .ssh import LoginOutcome, probe_login
from .vault import encrypt_file
from .workspace import Workspace

if TYPE_CHECKING:
    from pathlib import Path

    from .context import Context
    from .site import Site

MIN_PASSWORD_LENGTH = 20


def _label(site_name: str) -> str:
    return f"vb-operator-{site_name}-{datetime.now(tz=timezone.utc):%Y%m%d-%H%M%S}"


def _new_password(ctx: Context) -> str:
    password = ctx.prompt_secret("New vault password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        message = (
            f"Use a long random password of at least {MIN_PASSWORD_LENGTH} characters."
        )
        raise VbError(message)
    if ctx.prompt_secret("Repeat the vault password: ") != password:
        message = "The two passwords differ."
        raise VbError(message)
    return password


def _write_keys(site: Site, keys: list[str]) -> None:
    site.inventory.write_text(
        with_operator_keys(site.inventory.read_text(encoding="utf-8"), keys),
        encoding="utf-8",
    )


def cmd_init(ctx: Context, site_name: str) -> int:
    """Create a site from the template of vb-deploy, with a new operator key."""
    if not SITE_NAME.match(site_name):
        message = f"'{site_name}' is not a valid site name."
        raise VbError(message)
    require_tools(ctx, ["ansible-vault", "ssh-keygen"])
    template = ctx.deploy_dir / "site-template" / site_name
    if not template.is_dir():
        known = sorted(
            path.name for path in (ctx.deploy_dir / "site-template").iterdir()
        )
        message = (
            f"vb-deploy has no template for '{site_name}' (known: {', '.join(known)})."
        )
        raise VbError(message)
    target = ctx.sites_dir / site_name
    if target.exists():
        message = f"{target} already exists; nothing was changed."
        raise VbError(message)
    password = _new_password(ctx)
    _create_site(ctx, template, target, password)
    ctx.emit(
        f"Created {target}. Next: fill in the host, domains and mail address in "
        f"inventory.ini, then create the files in secrets/ from the .example files "
        f"with 'ansible-vault create' (same vault password)."
    )
    return 0


def _create_site(ctx: Context, template: Path, target: Path, password: str) -> None:
    completed = False
    try:
        with Workspace(ctx.workspace_base) as workspace:
            password_file = workspace.write_secret(
                "vault_pass", f"{password}\n".encode()
            )
            private_key, public_key = generate_key(
                ctx.runner, workspace.path, _label(target.name)
            )
            shutil.copytree(template, target)
            encrypt_file(
                ctx.runner, password_file, private_key, target / "operator_key.vault"
            )
            inventory = target / "inventory.ini"
            inventory.write_text(
                with_operator_keys(inventory.read_text(encoding="utf-8"), [public_key]),
                encoding="utf-8",
            )
        completed = True
    finally:
        if not completed:
            shutil.rmtree(target, ignore_errors=True)


def _run_setup(ctx: Context, site: Site, key: Path) -> None:
    result = ctx.runner.interactive(
        setup_argv(site=site, key=key, user="admin", ask_password=False, extra=[]),
        cwd=ctx.deploy_dir,
    )
    if result != 0:
        message = (
            f"The setup run failed (exit code {result}). The inventory lists the keys "
            f"of the step that failed; fix the cause and run './vb rotate-key' or "
            f"'./vb setup' again."
        )
        raise VbError(message)


def cmd_rotate_key(ctx: Context, site_name: str) -> int:
    """Replace the operator key in two steps, so there is always one that works."""
    site = load_site(ctx.sites_dir, site_name)
    require_tools(ctx, [*ANSIBLE_TOOLS, "ssh-keygen"])
    host = load_host(ctx, site)
    with unlocked(ctx, site) as access:
        require_login(ctx, host, "admin", access.key)
        old_public = public_key_of(ctx.runner, access.key)
        new_key, new_public = generate_key(
            ctx.runner, access.workspace.path, _label(site.name)
        )
        current = read_operator_keys(site.inventory.read_text(encoding="utf-8"))
        kept = [key for key in current if not same_key(key, old_public)]

        ctx.emit("Step 1: the host accepts the old and the new key.")
        _write_keys(site, [*current, new_public])
        _run_setup(ctx, site, access.key)
        require_login(ctx, host, "admin", new_key)
        require_login(ctx, host, "service", new_key)

        ctx.emit("Step 2: store the new key, then remove the old one from the host.")
        pending = site.key_vault.with_name("operator_key.vault.new")
        encrypt_file(ctx.runner, access.password_file, new_key, pending)
        pending.replace(site.key_vault)
        _write_keys(site, [*kept, new_public])
        _run_setup(ctx, site, new_key)

        if probe_login(ctx.runner, host, "admin", access.key) is LoginOutcome.OK:
            message = "The old key still logs in; the host was not updated as expected."
            raise VbError(message)
    ctx.emit("Done. Commit the changes in sites/ so the new key is not lost.")
    return 0
