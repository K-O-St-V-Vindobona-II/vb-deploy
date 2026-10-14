"""The everyday commands: setup, deploy, check and ssh."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .access import (
    AccessMode,
    detect_access_mode,
    require_key_listed,
    require_login,
)
from .context import load_host, require_tools, unlocked
from .errors import VbError
from .playbooks import deploy_argv, setup_argv
from .site import load_site
from .ssh import login_argv

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .context import Context

CHECK_ARGS = ["--check", "--diff"]
LOGIN_USERS = ("admin", "service")
ANSIBLE_TOOLS = ["ansible-playbook", "ansible-inventory", "ansible-vault", "ssh"]


def cmd_setup(ctx: Context, site_name: str, extra: Sequence[str]) -> int:
    """Run setup_vps.yml, logging in the way the host currently allows."""
    site = load_site(ctx.sites_dir, site_name)
    require_tools(ctx, ANSIBLE_TOOLS)
    host = load_host(ctx, site)
    with unlocked(ctx, site) as access:
        require_key_listed(ctx, site, access.key)
        mode = detect_access_mode(ctx, host, access.key)
        ctx.emit(f"Host state: {mode.value}.")
        ask_password = mode is AccessMode.ROOT_PROVIDER_LOGIN
        if ask_password:
            require_tools(ctx, ["sshpass"])
            ctx.emit(
                "Ansible asks for the root password of the provider next. If the "
                "provider marked that password as expired, Ansible cannot log in: "
                f"run 'ssh root@{host.address}' once, set a new password there, "
                "and use the new password here."
            )
        argv = setup_argv(
            site=site,
            key=access.key,
            user=mode.user,
            ask_password=ask_password,
            extra=extra,
        )
        return ctx.runner.interactive(argv, cwd=ctx.deploy_dir)


def cmd_deploy(ctx: Context, site_name: str, extra: Sequence[str]) -> int:
    """Run deploy.yml as the service user."""
    site = load_site(ctx.sites_dir, site_name)
    require_tools(ctx, ANSIBLE_TOOLS)
    host = load_host(ctx, site)
    with unlocked(ctx, site) as access:
        require_login(ctx, host, "service", access.key)
        argv = deploy_argv(
            site=site,
            key=access.key,
            vault_password_file=access.password_file,
            extra=extra,
        )
        return ctx.runner.interactive(argv, cwd=ctx.deploy_dir)


def cmd_check(ctx: Context, site_name: str, extra: Sequence[str]) -> int:
    """Preview both playbooks without changing anything on the host."""
    site = load_site(ctx.sites_dir, site_name)
    require_tools(ctx, ANSIBLE_TOOLS)
    host = load_host(ctx, site)
    with unlocked(ctx, site) as access:
        require_key_listed(ctx, site, access.key)
        require_login(ctx, host, "admin", access.key)
        require_login(ctx, host, "service", access.key)
        setup_result = ctx.runner.interactive(
            setup_argv(
                site=site,
                key=access.key,
                user="admin",
                ask_password=False,
                extra=[*CHECK_ARGS, *extra],
            ),
            cwd=ctx.deploy_dir,
        )
        if setup_result != 0:
            return setup_result
        return ctx.runner.interactive(
            deploy_argv(
                site=site,
                key=access.key,
                vault_password_file=access.password_file,
                extra=[*CHECK_ARGS, *extra],
            ),
            cwd=ctx.deploy_dir,
        )


def cmd_ssh(ctx: Context, site_name: str, user: str) -> int:
    """Open a shell on the host with the operator key."""
    if user not in LOGIN_USERS:
        message = f"The user must be one of: {', '.join(LOGIN_USERS)}."
        raise VbError(message)
    site = load_site(ctx.sites_dir, site_name)
    require_tools(ctx, ["ansible-inventory", "ansible-vault", "ssh"])
    host = load_host(ctx, site)
    with unlocked(ctx, site) as access:
        return ctx.runner.interactive(login_argv(host, user, access.key))
