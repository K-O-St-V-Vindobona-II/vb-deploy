"""setup, deploy, check and ssh against a scripted runner."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import (
    DENIED,
    OK,
    OLD_PUBLIC,
    PASSWORD,
    FakeRunner,
    Messages,
    build_runner,
    is_program,
    login_probe,
)
from vb_tools.access import (
    AccessMode,
    detect_access_mode,
    require_key_listed,
    require_login,
)
from vb_tools.commands import cmd_check, cmd_deploy, cmd_setup, cmd_ssh
from vb_tools.errors import VbError
from vb_tools.runner import RunResult
from vb_tools.site import Host, load_site

if TYPE_CHECKING:
    from collections.abc import Callable

    from vb_tools.context import Context

HOST = Host("web", "203.0.113.7", None)
KEY = Path("/ram/key")
CHANGED = RunResult(255, "", "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@")
REFUSED = RunResult(255, "", "ssh: connect to host x port 22: Connection refused")


class TestAccessMode:
    @pytest.mark.parametrize(
        ("logins", "expected", "user"),
        [
            ({"admin": OK, "root": DENIED}, AccessMode.ADMIN_KEY, "admin"),
            ({"admin": DENIED, "root": OK}, AccessMode.ROOT_KEY, "root"),
            ({"admin": DENIED, "root": DENIED}, AccessMode.ROOT_PROVIDER_LOGIN, "root"),
        ],
    )
    def test_the_first_login_that_works_decides(
        self,
        make_context: Callable[..., Context],
        logins: dict[str, RunResult],
        expected: AccessMode,
        user: str,
    ) -> None:
        ctx = make_context(build_runner(logins=logins))
        mode = detect_access_mode(ctx, HOST, KEY)
        assert (mode, mode.user) == (expected, user)

    def test_root_is_not_tried_when_admin_works(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner(logins={"admin": OK})
        detect_access_mode(make_context(runner), HOST, KEY)
        assert not any("root@" in " ".join(argv) for _, argv, _ in runner.calls)

    def test_a_changed_host_key_names_the_fix(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner(logins={"admin": CHANGED}))
        with pytest.raises(VbError, match=r"ssh-keygen -R 203\.0\.113\.7"):
            detect_access_mode(ctx, HOST, KEY)

    def test_an_unreachable_host_is_not_mistaken_for_a_new_one(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner(logins={"admin": REFUSED}))
        with pytest.raises(VbError, match=r"Cannot reach 203\.0\.113\.7"):
            detect_access_mode(ctx, HOST, KEY)

    def test_require_login_passes_and_fails(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner(logins={"service": OK, "admin": DENIED}))
        require_login(ctx, HOST, "service", KEY)
        with pytest.raises(
            VbError, match="does not accept the operator key for 'admin'"
        ):
            require_login(ctx, HOST, "admin", KEY)


class TestKeyMustBeListed:
    def test_a_listed_key_passes(self, make_context: Callable[..., Context]) -> None:
        ctx = make_context(build_runner())
        site = load_site(ctx.sites_dir, "production")
        require_key_listed(ctx, site, KEY)

    def test_the_comment_does_not_matter(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        runner.capture_rules.insert(
            0,
            (
                is_program("ssh-keygen", "-y"),
                RunResult(0, OLD_PUBLIC.rsplit(" ", 1)[0] + " other-comment\n", ""),
            ),
        )
        ctx = make_context(runner)
        require_key_listed(ctx, load_site(ctx.sites_dir, "production"), KEY)

    def test_an_unlisted_key_is_refused_before_it_could_be_removed(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        runner.capture_rules.insert(
            0,
            (
                is_program("ssh-keygen", "-y"),
                RunResult(0, "ssh-ed25519 AAAAUNLISTED x\n", ""),
            ),
        )
        ctx = make_context(runner)
        with pytest.raises(VbError, match="lock you out"):
            require_key_listed(ctx, load_site(ctx.sites_dir, "production"), KEY)

    @pytest.mark.parametrize("command", [cmd_setup, cmd_check])
    def test_setup_and_check_stop_before_touching_the_host(
        self, make_context: Callable[..., Context], command: Callable[..., int]
    ) -> None:
        runner = build_runner()
        runner.capture_rules.insert(
            0,
            (
                is_program("ssh-keygen", "-y"),
                RunResult(0, "ssh-ed25519 AAAAUNLISTED x\n", ""),
            ),
        )
        with pytest.raises(VbError, match="lock you out"):
            command(make_context(runner), "production", [])
        assert runner.interactive_calls() == []
        assert not any("BatchMode=yes" in argv for _, argv, _ in runner.calls)


def secret_check_runner(
    seen: list[dict[str, object]], **kwargs: RunResult
) -> FakeRunner:
    """A runner whose interactive call records what exists while ansible runs."""
    runner = build_runner(**kwargs)

    def interactive(argv: list[str]) -> int:
        key = Path(argv[argv.index("--private-key") + 1])
        seen.append(
            {
                "key_mode": stat.S_IMODE(key.stat().st_mode),
                "argv": argv,
                "cwd_files": sorted(path.name for path in key.parent.iterdir()),
            }
        )
        return 0

    runner.interactive_rule = interactive
    return runner


class TestSetup:
    def test_an_existing_host_is_set_up_as_admin_without_a_prompt(
        self, make_context: Callable[..., Context], messages: Messages
    ) -> None:
        seen: list[dict[str, object]] = []
        runner = secret_check_runner(seen)
        ctx = make_context(runner)
        assert cmd_setup(ctx, "production", ["--diff"]) == 0
        argv = runner.interactive_calls()[0]
        assert argv[argv.index("-u") + 1] == "admin"
        assert "--ask-pass" not in argv
        assert argv[-1] == "--diff"
        assert runner.calls[-1][2] == ctx.deploy_dir
        assert "admin with the operator key" in messages.lines[0]

    def test_a_new_host_asks_ansible_for_the_provider_password(
        self, make_context: Callable[..., Context], messages: Messages
    ) -> None:
        runner = secret_check_runner([], logins={"admin": DENIED, "root": DENIED})
        assert cmd_setup(make_context(runner), "production", []) == 0
        argv = runner.interactive_calls()[0]
        assert argv[argv.index("-u") + 1] == "root"
        assert "--ask-pass" in argv
        assert "provider" in messages.lines[-1]
        assert "expired" in messages.lines[-1]
        assert "ssh root@203.0.113.7" in messages.lines[-1]

    def test_the_password_prompt_needs_sshpass(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner(logins={"admin": DENIED, "root": DENIED})
        ctx = make_context(runner, missing_tools=("sshpass",))
        with pytest.raises(VbError, match="sshpass"):
            cmd_setup(ctx, "production", [])
        assert runner.interactive_calls() == []

    def test_the_key_is_private_and_the_password_never_on_a_command_line(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        seen: list[dict[str, object]] = []
        runner = secret_check_runner(seen)
        cmd_setup(make_context(runner), "production", [])
        assert seen[0]["key_mode"] == 0o600
        assert not any(PASSWORD in " ".join(argv) for _, argv, _ in runner.calls)
        assert list(workspace_base.iterdir()) == []

    def test_the_exit_code_of_ansible_is_returned(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        runner.interactive_rule = lambda _argv: 2
        assert cmd_setup(make_context(runner), "production", []) == 2


class TestDeploy:
    def test_deploy_runs_with_the_vault_file_and_the_site_directory(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        runner = build_runner()
        ctx = make_context(runner)
        assert cmd_deploy(ctx, "production", ["--tags", "deploy-api"]) == 0
        argv = runner.interactive_calls()[0]
        assert "playbooks/deploy.yml" in argv
        assert str(ctx.sites_dir / "production") in argv[argv.index("-e") + 1]
        assert argv[-2:] == ["--tags", "deploy-api"]
        assert list(workspace_base.iterdir()) == []

    def test_a_host_that_does_not_know_the_key_points_to_setup(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner(logins={"service": DENIED})
        with pytest.raises(VbError, match=r"\./vb setup"):
            cmd_deploy(make_context(runner), "production", [])
        assert runner.interactive_calls() == []

    def test_an_unknown_site_is_reported_before_the_password_is_asked(
        self, make_context: Callable[..., Context]
    ) -> None:
        asked: list[str] = []
        ctx = make_context(build_runner())
        ctx.prompt_secret = lambda prompt: asked.append(prompt) or PASSWORD
        with pytest.raises(VbError, match="no site 'qa'"):
            cmd_deploy(ctx, "qa", [])
        assert asked == []


class TestCheck:
    def test_both_playbooks_run_in_check_mode_with_diff(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        assert cmd_check(make_context(runner), "production", ["-v"]) == 0
        setup_argv, deploy_argv = runner.interactive_calls()
        for argv in (setup_argv, deploy_argv):
            assert argv[-3:] == ["--check", "--diff", "-v"]
        assert "playbooks/setup_vps.yml" in setup_argv
        assert "playbooks/deploy.yml" in deploy_argv

    def test_a_failed_preview_of_setup_stops_the_second_one(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner()
        runner.interactive_rule = lambda _argv: 4
        assert cmd_check(make_context(runner), "production", []) == 4
        assert len(runner.interactive_calls()) == 1

    def test_a_host_that_is_not_set_up_is_reported(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner(logins={"admin": DENIED, "service": DENIED})
        with pytest.raises(VbError, match=r"\./vb setup"):
            cmd_check(make_context(runner), "production", [])


class TestSsh:
    def test_a_shell_opens_with_the_operator_key(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        runner = build_runner()
        assert cmd_ssh(make_context(runner), "production", "admin") == 0
        argv = runner.interactive_calls()[0]
        assert argv[0] == "ssh"
        assert argv[-1] == "admin@203.0.113.7"
        assert list(workspace_base.iterdir()) == []

    def test_only_the_two_accounts_are_allowed(
        self, make_context: Callable[..., Context]
    ) -> None:
        with pytest.raises(VbError, match="must be one of"):
            cmd_ssh(make_context(build_runner()), "production", "root")


def test_the_login_probe_matcher_distinguishes_users() -> None:
    assert login_probe("admin")(["ssh", "BatchMode=yes", "admin@h", "true"])
    assert not login_probe("admin")(["ssh", "BatchMode=yes", "root@h", "true"])
