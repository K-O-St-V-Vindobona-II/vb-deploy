"""Argument parsing, error handling and signal handling of the command line."""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

import pytest

from tests.conftest import build_runner
from vb_tools import cli
from vb_tools.cli import build_parser, exit_on_termination, main
from vb_tools.errors import VbError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from vb_tools.context import Context


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
