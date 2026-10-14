"""Workspace, runner, vault, keys, ssh and playbook argument builders."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tests.conftest import FakeRunner, is_program
from vb_tools import runner as runner_module
from vb_tools import workspace as workspace_module
from vb_tools.errors import VbError
from vb_tools.keys import (
    generate_key,
    key_identity,
    normalize_private_key,
    public_key_of,
    same_key,
)
from vb_tools.playbooks import deploy_argv, setup_argv
from vb_tools.runner import RunResult, SubprocessRunner
from vb_tools.site import Host, Site
from vb_tools.ssh import (
    LoginOutcome,
    classify,
    identity_options,
    login_argv,
    probe_login,
)
from vb_tools.vault import decrypt_to_text, encrypt_file
from vb_tools.workspace import Workspace

KEY = Path("/ram/key")
HOST = Host(name="web", address="203.0.113.7", port=None)
SITE = Site(name="production", directory=Path("/access/sites/production"))


class TestWorkspace:
    def test_directory_and_secret_are_private(self, tmp_path: Path) -> None:
        with Workspace(tmp_path) as workspace:
            secret = workspace.write_secret("key", b"data")
            assert stat.S_IMODE(workspace.path.stat().st_mode) == 0o700
            assert stat.S_IMODE(secret.stat().st_mode) == 0o600
            assert secret.read_bytes() == b"data"

    def test_everything_is_removed_on_exit(self, tmp_path: Path) -> None:
        with Workspace(tmp_path) as workspace:
            workspace.write_secret("key", b"data")
        assert list(tmp_path.iterdir()) == []

    def test_everything_is_removed_when_the_block_fails(self, tmp_path: Path) -> None:
        def fail_inside_the_block() -> None:
            with Workspace(tmp_path) as workspace:
                workspace.write_secret("key", b"data")
                raise RuntimeError

        with pytest.raises(RuntimeError):
            fail_inside_the_block()
        assert list(tmp_path.iterdir()) == []

    def test_a_secret_is_never_overwritten(self, tmp_path: Path) -> None:
        with Workspace(tmp_path) as workspace:
            workspace.write_secret("key", b"one")
            with pytest.raises(FileExistsError):
                workspace.write_secret("key", b"two")

    def test_a_secret_name_cannot_contain_a_path(self, tmp_path: Path) -> None:
        with Workspace(tmp_path) as workspace, pytest.raises(ValueError, match="path"):
            workspace.write_secret("../escape", b"x")

    def test_ram_backed_storage_is_preferred(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(workspace_module, "SHARED_MEMORY", tmp_path)
        assert workspace_module.secure_base() == tmp_path

    def test_the_temp_directory_is_the_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(workspace_module, "SHARED_MEMORY", tmp_path / "missing")
        assert workspace_module.secure_base() == Path(
            workspace_module.tempfile.gettempdir()
        )


class TestSubprocessRunner:
    def test_capture_returns_output_and_code(self) -> None:
        result = SubprocessRunner().capture(
            [sys.executable, "-c", "import sys; print('out'); sys.exit(3)"]
        )
        assert (result.returncode, result.stdout.strip()) == (3, "out")

    def test_interactive_returns_the_exit_code(self) -> None:
        runner = SubprocessRunner()
        assert runner.interactive([sys.executable, "-c", "pass"]) == 0
        assert runner.interactive([sys.executable, "-c", "raise SystemExit(4)"]) == 4

    def test_a_missing_program_is_reported(self) -> None:
        runner = SubprocessRunner()
        with pytest.raises(VbError, match="not installed"):
            runner.capture(["vb-no-such-program"])
        with pytest.raises(VbError, match="not installed"):
            runner.interactive(["vb-no-such-program"])

    def test_a_hanging_program_is_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runner_module, "CAPTURE_TIMEOUT_SECONDS", 0.2)
        with pytest.raises(VbError, match="did not finish"):
            SubprocessRunner().capture(
                [sys.executable, "-c", "import time; time.sleep(5)"]
            )


class TestVault:
    def test_decrypt_goes_to_stdout_and_returns_the_text(self) -> None:
        runner = FakeRunner([(is_program("ansible-vault"), RunResult(0, "plain", ""))])
        assert decrypt_to_text(runner, Path("/ram/pw"), Path("/s/key.vault")) == "plain"
        assert runner.calls[0][1] == [
            "ansible-vault",
            "decrypt",
            "--vault-password-file",
            "/ram/pw",
            "--output",
            "-",
            "/s/key.vault",
        ]

    def test_a_failed_decrypt_names_the_file_not_the_password(self) -> None:
        runner = FakeRunner([(is_program("ansible-vault"), RunResult(1, "", "boom"))])
        with pytest.raises(VbError, match=r"key\.vault: wrong vault password"):
            decrypt_to_text(runner, Path("/ram/pw"), Path("/s/key.vault"))

    def test_encrypt_writes_to_the_destination(self) -> None:
        runner = FakeRunner([(is_program("ansible-vault"), RunResult(0, "", ""))])
        encrypt_file(runner, Path("/ram/pw"), Path("/ram/k"), Path("/s/new.vault"))
        assert runner.calls[0][1][-3:] == ["--output", "/s/new.vault", "/ram/k"]

    def test_a_failed_encrypt_is_reported(self) -> None:
        runner = FakeRunner([(is_program("ansible-vault"), RunResult(1, "", "no way"))])
        with pytest.raises(VbError, match="no way"):
            encrypt_file(runner, Path("/ram/pw"), Path("/ram/k"), Path("/s/new.vault"))


PRIVATE = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"


class TestKeys:
    def test_a_key_gets_exactly_one_final_newline(self) -> None:
        assert normalize_private_key(f"\n{PRIVATE}\n\n") == PRIVATE + "\n"

    @pytest.mark.parametrize(
        "text", ["", "hello", "-----BEGIN OPENSSH PRIVATE KEY-----"]
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(VbError, match="OpenSSH private key"):
            normalize_private_key(text)

    def test_the_comment_does_not_change_the_identity(self) -> None:
        assert key_identity("ssh-ed25519 AAAA one") == ("ssh-ed25519", "AAAA")
        assert same_key("ssh-ed25519 AAAA one", "ssh-ed25519 AAAA two")
        assert not same_key("ssh-ed25519 AAAA one", "ssh-ed25519 BBBB one")

    def test_a_line_without_a_body_is_not_a_key(self) -> None:
        with pytest.raises(VbError, match="Not a public key"):
            key_identity("ssh-ed25519")

    def test_the_public_key_is_derived_from_the_private_one(self) -> None:
        runner = FakeRunner(
            [(is_program("ssh-keygen", "-y"), RunResult(0, "ssh-ed25519 AAAA c\n", ""))]
        )
        assert public_key_of(runner, KEY) == "ssh-ed25519 AAAA c"

    def test_an_unreadable_key_is_reported(self) -> None:
        runner = FakeRunner([(is_program("ssh-keygen"), RunResult(1, "", "bad"))])
        with pytest.raises(VbError, match="could not read"):
            public_key_of(runner, KEY)

    def test_generation_returns_both_parts(self, tmp_path: Path) -> None:
        def create(argv: list[str]) -> RunResult:
            Path(argv[-1]).write_text("private")
            Path(argv[-1] + ".pub").write_text("ssh-ed25519 AAAA label\n")
            return RunResult(0, "", "")

        runner = FakeRunner([(is_program("ssh-keygen"), create)])
        private, public = generate_key(runner, tmp_path, "label")
        assert (private.read_text(), public) == ("private", "ssh-ed25519 AAAA label")
        assert runner.calls[0][1][:8] == [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "label",
        ]

    def test_a_failed_generation_is_reported(self, tmp_path: Path) -> None:
        runner = FakeRunner([(is_program("ssh-keygen"), RunResult(1, "", "nope"))])
        with pytest.raises(VbError, match="nope"):
            generate_key(runner, tmp_path, "label")


class TestSsh:
    @pytest.mark.parametrize(
        ("code", "stderr", "expected"),
        [
            (0, "", LoginOutcome.OK),
            (255, "x: Permission denied (publickey).", LoginOutcome.DENIED),
            (
                255,
                "@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @",
                LoginOutcome.HOST_KEY_CHANGED,
            ),
            (255, "Host key verification failed.", LoginOutcome.HOST_KEY_CHANGED),
            (
                255,
                "ssh: connect to host x port 22: Connection refused",
                LoginOutcome.UNREACHABLE,
            ),
            (255, "ssh: Could not resolve hostname x", LoginOutcome.UNREACHABLE),
        ],
    )
    def test_the_outcome_is_told_apart(
        self, code: int, stderr: str, expected: LoginOutcome
    ) -> None:
        assert classify(RunResult(code, "", stderr)) is expected

    def test_only_the_given_key_is_offered(self) -> None:
        options = identity_options(HOST, KEY)
        assert options[:4] == ["-i", "/ram/key", "-o", "IdentitiesOnly=yes"]
        assert "-p" not in options

    def test_a_custom_port_is_passed(self) -> None:
        options = identity_options(Host("web", "203.0.113.7", 2222), KEY)
        assert options[-2:] == ["-p", "2222"]

    def test_the_probe_never_asks_for_a_password(self) -> None:
        runner = FakeRunner([(is_program("ssh"), RunResult(0, "", ""))])
        assert probe_login(runner, HOST, "admin", KEY) is LoginOutcome.OK
        argv = runner.calls[0][1]
        assert {"BatchMode=yes", "PreferredAuthentications=publickey"} <= set(argv)
        assert argv[-2:] == ["admin@203.0.113.7", "true"]

    def test_the_interactive_login_has_no_command(self) -> None:
        assert login_argv(HOST, "service", KEY)[-1] == "service@203.0.113.7"


class TestPlaybooks:
    def test_setup_as_admin_has_no_password_prompt(self) -> None:
        argv = setup_argv(
            site=SITE, key=KEY, user="admin", ask_password=False, extra=[]
        )
        assert argv == [
            "ansible-playbook",
            "-i",
            "/access/sites/production/inventory.ini",
            "playbooks/setup_vps.yml",
            "-u",
            "admin",
            "--private-key",
            "/ram/key",
        ]

    def test_setup_as_root_can_ask_for_the_provider_password(self) -> None:
        argv = setup_argv(
            site=SITE, key=KEY, user="root", ask_password=True, extra=["--check"]
        )
        assert argv[-2:] == ["--ask-pass", "--check"]

    def test_deploy_passes_the_site_directory_and_the_vault_file(self) -> None:
        argv = deploy_argv(
            site=SITE,
            key=KEY,
            vault_password_file=Path("/ram/pw"),
            extra=["--tags", "x"],
        )
        assert argv[argv.index("-e") + 1] == '{"site_dir": "/access/sites/production"}'
        assert argv[argv.index("--vault-password-file") + 1] == "/ram/pw"
        assert argv[-2:] == ["--tags", "x"]
        assert "-u" not in argv
