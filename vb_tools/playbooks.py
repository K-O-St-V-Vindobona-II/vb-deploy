"""The ansible-playbook command lines for the two playbooks of vb-deploy."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .site import Site


def setup_argv(
    *,
    site: Site,
    key: Path,
    user: str,
    ask_password: bool,
    extra: Sequence[str],
) -> list[str]:
    """setup_vps.yml; the connection password is asked by ansible itself."""
    argv = [
        "ansible-playbook",
        "-i",
        str(site.inventory),
        "playbooks/setup_vps.yml",
        "-u",
        user,
        "--private-key",
        str(key),
    ]
    if ask_password:
        argv.append("--ask-pass")
    return [*argv, *extra]


def deploy_argv(
    *, site: Site, key: Path, vault_password_file: Path, extra: Sequence[str]
) -> list[str]:
    """deploy.yml; it connects as service, which the playbook itself sets."""
    return [
        "ansible-playbook",
        "-i",
        str(site.inventory),
        "playbooks/deploy.yml",
        "-e",
        json.dumps({"site_dir": str(site.directory)}),
        "--private-key",
        str(key),
        "--vault-password-file",
        str(vault_password_file),
        *extra,
    ]
