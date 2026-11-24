"""Argument parsing, error handling and signal handling of the command line."""

from __future__ import annotations

import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import build_runner
from vb_tools import cli
from vb_tools.cli import build_parser, exit_on_termination, main
from vb_tools.errors import VbError

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from vb_tools.context import Context

ROOT = Path(__file__).resolve().parent.parent


class TestParser:
    def test_ansible_options_after_the_site_are_passed_on(self) -> None:
        arguments = build_parser().parse_args(["deploy", "production", "--tags", "x"])
        assert (arguments.site, arguments.ansible_args) == (
            "production",
            ["--tags", "x"],
        )

    def test_ssh_defaults_to_the_service_user(self) -> None:
        assert build_parser().parse_args(["ssh", "production"]).user == "service"

    def test_ssh_accepts_only_the_two_accounts(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["ssh", "production", "root"])

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


def _command_parsers() -> dict[str, argparse.ArgumentParser]:
    return build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr,return-value]


def _deploy_help() -> str:
    return _command_parsers()["deploy"].format_help()


class TestHelp:
    @pytest.mark.parametrize(
        "command", ["setup", "deploy", "check", "ssh", "init", "rotate-key"]
    )
    def test_every_argument_of_the_command_is_explained(self, command: str) -> None:
        unexplained = [
            action.dest
            for action in _command_parsers()[command]._actions
            if not action.help
        ]
        assert unexplained == []

    def test_the_deploy_help_names_exactly_the_tags_of_the_playbook(self) -> None:
        playbook = (ROOT / "playbooks" / "deploy.yml").read_text()
        playbook_tags = set(re.findall(r"tags: \[never, (deploy-[a-z]+)\]", playbook))

        named = set(re.findall(r"--tags (deploy-[a-z]+)", _deploy_help()))

        assert playbook_tags
        assert named == playbook_tags

    def test_the_image_variables_of_the_deploy_help_exist(self) -> None:
        templates = "".join(
            path.read_text() for path in (ROOT / "templates").glob("*.j2")
        )
        named = set(re.findall(r"vb_[a-z]+_image_tag", _deploy_help()))

        assert named == {"vb_api_image_tag", "vb_intern_image_tag", "vb_www_image_tag"}
        assert all(name in templates for name in named)


class TestMain:
    @pytest.mark.parametrize(
        "command", ["setup", "deploy", "check", "ssh", "init", "rotate-key"]
    )
    def test_every_command_is_wired_to_its_handler(
        self,
        command: str,
        monkeypatch: pytest.MonkeyPatch,
        make_context: Callable[..., Context],
    ) -> None:
        called: list[str] = []
        name = {"rotate-key": "cmd_rotate_key"}.get(command, f"cmd_{command}")
        monkeypatch.setattr(cli, name, lambda *_args: called.append(command) or 0)
        ctx = make_context(build_runner())
        assert main([command, "production"], ctx) == 0
        assert called == [command]

    def test_an_operator_error_prints_a_message_and_returns_one(
        self,
        capsys: pytest.CaptureFixture[str],
        make_context: Callable[..., Context],
    ) -> None:
        assert main(["ssh", "nosuch"], make_context(build_runner())) == 1
        assert "vb: There is no site 'nosuch'" in capsys.readouterr().err

    def test_ctrl_c_returns_130(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        make_context: Callable[..., Context],
    ) -> None:
        def interrupted(*_args: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "cmd_setup", interrupted)
        assert main(["setup", "production"], make_context(build_runner())) == 130
        assert "interrupted" in capsys.readouterr().err

    def test_the_real_context_is_built_when_none_is_given(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--sites-dir", str(tmp_path), "ssh", "nosuch"]) == 1
        assert "no site 'nosuch'" in capsys.readouterr().err

    def test_messages_go_to_standard_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.write_line("hello")
        assert capsys.readouterr().out == "hello\n"


class TestDefaultSitesDir:
    def test_the_sites_of_the_neighbouring_private_repository_are_used(
        self, tmp_path: Path
    ) -> None:
        deploy = tmp_path / "vb-deploy"
        assert cli.default_sites_dir(deploy) == tmp_path / "vb-access" / "sites"


class TestTermination:
    def test_sigterm_ends_the_program_through_a_normal_exit(self) -> None:
        with pytest.raises(SystemExit) as stop, exit_on_termination():
            signal.raise_signal(signal.SIGTERM)
        assert stop.value.code == 128 + signal.SIGTERM

    def test_the_cleanup_of_the_block_still_runs(self, tmp_path: Path) -> None:
        marker = tmp_path / "cleaned"

        def terminated_block() -> None:
            try:
                with exit_on_termination():
                    signal.raise_signal(signal.SIGHUP)
            finally:
                marker.write_text("done")

        with pytest.raises(SystemExit):
            terminated_block()
        assert marker.exists()

    def test_the_previous_handlers_are_restored(self) -> None:
        before = signal.getsignal(signal.SIGTERM)
        with exit_on_termination():
            assert signal.getsignal(signal.SIGTERM) is not before
        assert signal.getsignal(signal.SIGTERM) is before


def test_an_operator_error_can_be_raised_with_a_message() -> None:
    assert str(VbError("text")) == "text"
