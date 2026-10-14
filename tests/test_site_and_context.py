"""Site files, the inventory, and the unlocked key."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from tests.conftest import (
    FAKE_KEY,
    INVENTORY_JSON,
    INVENTORY_TEXT,
    OLD_PUBLIC,
    OTHER_PUBLIC,
    FakeRunner,
    Messages,
    build_runner,
    is_program,
    vault_decrypt_rule,
)
from vb_tools.context import load_host, require_tools, unlocked
from vb_tools.errors import VbError
from vb_tools.runner import RunResult
from vb_tools.site import (
    Site,
    load_site,
    parse_single_host,
    read_operator_keys,
    with_operator_keys,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from vb_tools.context import Context


class TestLoadSite:
    def test_an_existing_site_is_found(self, tmp_path: Path) -> None:
        directory = tmp_path / "qa"
        directory.mkdir()
        (directory / "inventory.ini").write_text("[vps]\n")
        site = load_site(tmp_path, "qa")
        assert site.inventory == directory / "inventory.ini"
        assert site.key_vault == directory / "operator_key.vault"
        assert site.secrets == directory / "secrets"

    @pytest.mark.parametrize("name", ["", "../etc", "Prod", "a b", "1qa", "a" * 40])
    def test_a_name_that_could_leave_the_folder_is_refused(
        self, tmp_path: Path, name: str
    ) -> None:
        with pytest.raises(VbError, match="not a valid site name"):
            load_site(tmp_path, name)

    def test_an_unknown_site_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(VbError, match="no site 'qa'"):
            load_site(tmp_path, "qa")


def host_json(hostvars: dict[str, object]) -> str:
    return json.dumps({"_meta": {"hostvars": hostvars}})


class TestParseSingleHost:
    def test_address_and_port_come_from_the_host_variables(self) -> None:
        host = parse_single_host(
            host_json({"web": {"ansible_host": "203.0.113.7", "ansible_port": "2222"}})
        )
        assert (host.name, host.address, host.port) == ("web", "203.0.113.7", 2222)

    def test_the_name_is_the_address_by_default(self) -> None:
        host = parse_single_host(host_json({"web.example.org": {}}))
        assert (host.address, host.port) == ("web.example.org", None)

    @pytest.mark.parametrize("hostvars", [{}, {"a": {}, "b": {}}])
    def test_exactly_one_host_is_required(self, hostvars: dict[str, object]) -> None:
        with pytest.raises(VbError, match="exactly one host"):
            parse_single_host(host_json(hostvars))

    @pytest.mark.parametrize(
        "text", ["not json", "[]", "{}", '{"_meta": {}}', host_json({"web": "text"})]
    )
    def test_unexpected_output_is_reported(self, text: str) -> None:
        with pytest.raises(VbError, match="inventory"):
            parse_single_host(text)


class TestOperatorKeys:
    def test_the_keys_are_read_from_the_inventory(self) -> None:
        assert read_operator_keys(INVENTORY_TEXT) == [OLD_PUBLIC, OTHER_PUBLIC]

    def test_the_line_is_replaced_and_the_rest_is_kept(self) -> None:
        text = with_operator_keys(INVENTORY_TEXT, ['ssh-ed25519 A "q" \\ x'])
        assert read_operator_keys(text) == ['ssh-ed25519 A "q" \\ x']
        assert text.startswith("[vps]\nweb.example.org\n")
        assert "app_environment=production" in text

    def test_replacing_a_missing_line_fails_instead_of_doing_nothing(self) -> None:
        with pytest.raises(VbError, match="exactly one operator_public_keys"):
            with_operator_keys("[vps]\n", ["ssh-ed25519 A x"])

    @pytest.mark.parametrize(
        "text",
        [
            "[vps]\n",
            "operator_public_keys=[]\noperator_public_keys=[]\n",
            "operator_public_keys=oops\n",
            'operator_public_keys="text"\n',
            "operator_public_keys=[1]\n",
        ],
    )
    def test_a_missing_or_broken_line_is_refused(self, text: str) -> None:
        with pytest.raises(VbError, match="operator_public_keys"):
            read_operator_keys(text)


class TestContext:
    def test_missing_programs_are_listed_with_a_hint(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(FakeRunner(), missing_tools=("sshpass", "ssh"))
        with pytest.raises(VbError) as error:
            require_tools(ctx, ["ansible-vault", "sshpass", "ssh"])
        assert "sshpass: Install" in str(error.value)
        assert "ssh: Install" in str(error.value)
        assert "ansible-vault" not in str(error.value)

    def test_present_programs_pass(self, make_context: Callable[..., Context]) -> None:
        require_tools(make_context(FakeRunner()), ["ssh", "ansible-vault"])

    def test_the_host_is_read_in_the_deploy_directory(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        ctx = make_context(runner)
        host = load_host(ctx, Site("production", ctx.sites_dir / "production"))
        assert host.address == "203.0.113.7"
        assert runner.calls[0][2] == ctx.deploy_dir

    def test_an_unreadable_inventory_is_reported(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = FakeRunner(
            [(is_program("ansible-inventory"), RunResult(1, "", "bad ini"))]
        )
        ctx = make_context(runner)
        with pytest.raises(VbError, match="bad ini"):
            load_host(ctx, Site("production", ctx.sites_dir / "production"))

    def test_the_key_exists_privately_only_inside_the_block(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        ctx = make_context(build_runner())
        site = Site("production", ctx.sites_dir / "production")
        with unlocked(ctx, site) as access:
            assert access.key.read_text() == FAKE_KEY + "\n"
            assert stat.S_IMODE(access.key.stat().st_mode) == 0o600
            assert access.password_file.read_text().endswith("\n")
            assert stat.S_IMODE(access.password_file.stat().st_mode) == 0o600
        assert list(workspace_base.iterdir()) == []

    def test_nothing_is_left_behind_when_the_block_fails(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        ctx = make_context(build_runner())
        site = Site("production", ctx.sites_dir / "production")
        with pytest.raises(RuntimeError), unlocked(ctx, site):
            raise RuntimeError
        assert list(workspace_base.iterdir()) == []

    def test_an_empty_password_is_refused_before_anything_is_written(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        ctx = make_context(build_runner(), password="")
        site = Site("production", ctx.sites_dir / "production")
        with pytest.raises(VbError, match="must not be empty"), unlocked(ctx, site):
            pass
        assert list(workspace_base.iterdir()) == []

    def test_a_wrong_password_leaves_nothing_behind(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        runner = FakeRunner([(is_program("ansible-vault"), RunResult(1, "", "no"))])
        ctx = make_context(runner)
        site = Site("production", ctx.sites_dir / "production")
        with pytest.raises(VbError, match="wrong vault password"), unlocked(ctx, site):
            pass
        assert list(workspace_base.iterdir()) == []

    def test_a_file_that_is_not_a_key_is_refused(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        runner = FakeRunner([vault_decrypt_rule("just some text")])
        ctx = make_context(runner)
        site = Site("production", ctx.sites_dir / "production")
        with pytest.raises(VbError, match="OpenSSH private key"), unlocked(ctx, site):
            pass
        assert list(workspace_base.iterdir()) == []


def test_the_inventory_fixture_lists_one_host() -> None:
    assert parse_single_host(INVENTORY_JSON).address == "203.0.113.7"


def test_messages_collect_lines(messages: Messages) -> None:
    messages("one")
    assert messages.lines == ["one"]
