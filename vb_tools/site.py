"""A stage directory under sites/ and the facts read from its inventory."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .errors import VbError

if TYPE_CHECKING:
    from pathlib import Path

SITE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
OPERATOR_KEYS_LINE = re.compile(r"^operator_public_keys=.*$", re.MULTILINE)


@dataclass(frozen=True)
class Site:
    """One stage (production, qa, test) with its inventory, key and secrets."""

    name: str
    directory: Path

    @property
    def inventory(self) -> Path:
        """The inventory file of the stage."""
        return self.directory / "inventory.ini"

    @property
    def key_vault(self) -> Path:
        """The vault-encrypted private operator key."""
        return self.directory / "operator_key.vault"

    @property
    def secrets(self) -> Path:
        """The folder with the vault-encrypted environment files."""
        return self.directory / "secrets"


@dataclass(frozen=True)
class Host:
    """The single server of a stage."""

    name: str
    address: str
    port: int | None


def load_site(sites_dir: Path, name: str) -> Site:
    """Find an existing stage directory; the name is validated against traversal."""
    if not SITE_NAME.match(name):
        message = f"'{name}' is not a valid site name (lowercase letters, digits, '-')."
        raise VbError(message)
    site = Site(name=name, directory=sites_dir / name)
    if not site.inventory.is_file():
        message = f"There is no site '{name}': {site.inventory} does not exist."
        raise VbError(message)
    return site


def _mapping(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"Unexpected inventory output: {what} is not an object."
        raise VbError(message)
    return cast("dict[str, object]", value)


def parse_single_host(inventory_json: str) -> Host:
    """Read the one host out of the JSON that ansible-inventory --list prints."""
    try:
        document = _mapping(json.loads(inventory_json), "the document")
    except json.JSONDecodeError as error:
        message = "The inventory could not be read (ansible-inventory printed no JSON)."
        raise VbError(message) from error
    hostvars = _mapping(
        _mapping(document.get("_meta"), "_meta").get("hostvars"), "hostvars"
    )
    if len(hostvars) != 1:
        message = f"A site must list exactly one host, found {len(hostvars)}."
        raise VbError(message)
    name, raw_vars = next(iter(hostvars.items()))
    variables = _mapping(raw_vars, f"the variables of {name}")
    port = variables.get("ansible_port")
    return Host(
        name=name,
        address=str(variables.get("ansible_host", name)),
        port=None if port is None else int(str(port)),
    )


def read_operator_keys(inventory_text: str) -> list[str]:
    """Return the public keys listed in the inventory."""
    matches = OPERATOR_KEYS_LINE.findall(inventory_text)
    if len(matches) != 1:
        message = "The inventory needs exactly one operator_public_keys= line."
        raise VbError(message)
    try:
        keys = cast("object", json.loads(matches[0].split("=", 1)[1]))
    except json.JSONDecodeError as error:
        message = "operator_public_keys= must be a JSON list of strings."
        raise VbError(message) from error
    if not isinstance(keys, list) or not all(
        isinstance(key, str) for key in cast("list[object]", keys)
    ):
        message = "operator_public_keys= must be a JSON list of strings."
        raise VbError(message)
    return cast("list[str]", keys)


def with_operator_keys(inventory_text: str, keys: list[str]) -> str:
    """Return the inventory text with the key list replaced."""
    read_operator_keys(inventory_text)
    line = f"operator_public_keys={json.dumps(keys)}"
    return OPERATOR_KEYS_LINE.sub(lambda _match: line, inventory_text)
