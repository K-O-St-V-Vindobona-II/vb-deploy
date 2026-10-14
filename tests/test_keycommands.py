"""init and rotate-key: scripted runs, plus a real run with the real tools."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import (
    FAKE_KEY,
    NEW_PUBLIC,
    OLD_PUBLIC,
    OTHER_PUBLIC,
    PASSWORD,
    FakeRunner,
    Messages,
    build_runner,
    is_program,
    login_probe,
)
from vb_tools.context import Context
from vb_tools.errors import VbError
from vb_tools.keycommands import cmd_init, cmd_rotate_key
from vb_tools.runner import RunResult, SubprocessRunner
from vb_tools.site import read_operator_keys

if TYPE_CHECKING:
    from collections.abc import Callable

TEMPLATE_INVENTORY = (
    "[vps]\nCHANGEME.example.invalid\n\n[vps:vars]\napp_environment=qa\n"
    'operator_public_keys=["ssh-ed25519 CHANGEME operator"]\n'
)


def make_template(deploy_dir: Path) -> None:
    template = deploy_dir / "site-template" / "qa"
    (template / "secrets").mkdir(parents=True)
    (template / "inventory.ini").write_text(TEMPLATE_INVENTORY)
    (template / "secrets" / "vb-api.env.j2.example").write_text("A=change-me\n")


def keygen_and_vault_runner() -> FakeRunner:
    """ssh-keygen creates a pair, ansible-vault encrypt writes a marker file."""

    def generate(argv: list[str]) -> RunResult:
        Path(argv[-1]).write_text("PRIVATE")
        Path(argv[-1] + ".pub").write_text(f"{NEW_PUBLIC}\n")
        return RunResult(0, "", "")

    def encrypt(argv: list[str]) -> RunResult:
        Path(argv[argv.index("--output") + 1]).write_text("NEWENC")
        return RunResult(0, "", "")

    return FakeRunner(
        [
            (is_program("ssh-keygen", "-q"), generate),
            (is_program("ansible-vault", "encrypt"), encrypt),
        ]
    )


class TestInit:
    def test_a_site_is_created_with_a_new_key_and_the_inventory_points_to_it(
        self,
        make_context: Callable[..., Context],
        messages: Messages,
        workspace_base: Path,
    ) -> None:
        ctx = make_context(keygen_and_vault_runner())
        make_template(ctx.deploy_dir)
        assert cmd_init(ctx, "qa") == 0
        site = ctx.sites_dir / "qa"
        assert (site / "operator_key.vault").read_text() == "NEWENC"
        assert (site / "secrets" / "vb-api.env.j2.example").is_file()
        assert read_operator_keys((site / "inventory.ini").read_text()) == [NEW_PUBLIC]
        assert "CHANGEME.example.invalid" in (site / "inventory.ini").read_text()
        assert "inventory.ini" in messages.lines[-1]
        assert list(workspace_base.iterdir()) == []

    def test_a_failed_encryption_leaves_no_half_made_site(
        self, make_context: Callable[..., Context], workspace_base: Path
    ) -> None:
        runner = keygen_and_vault_runner()
        runner.capture_rules[1] = (is_program("ansible-vault"), RunResult(1, "", "no"))
        ctx = make_context(runner)
        make_template(ctx.deploy_dir)
        with pytest.raises(VbError, match="Cannot encrypt"):
            cmd_init(ctx, "qa")
        assert not (ctx.sites_dir / "qa").exists()
        assert list(workspace_base.iterdir()) == []

    def test_a_short_password_is_refused_before_anything_is_created(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(keygen_and_vault_runner(), password="short")
        make_template(ctx.deploy_dir)
        with pytest.raises(VbError, match="at least 20 characters"):
            cmd_init(ctx, "qa")
        assert not (ctx.sites_dir / "qa").exists()

    def test_a_mistyped_repetition_is_refused(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(keygen_and_vault_runner())
        make_template(ctx.deploy_dir)
        answers = iter([PASSWORD, PASSWORD + "x"])
        ctx.prompt_secret = lambda _prompt: next(answers)
        with pytest.raises(VbError, match="differ"):
            cmd_init(ctx, "qa")

    def test_an_existing_site_is_never_touched(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(keygen_and_vault_runner())
        make_template(ctx.deploy_dir)
        (ctx.sites_dir / "qa").mkdir()
        with pytest.raises(VbError, match="already exists"):
            cmd_init(ctx, "qa")

    def test_an_unknown_stage_lists_the_known_ones(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(keygen_and_vault_runner())
        make_template(ctx.deploy_dir)
        with pytest.raises(VbError, match=r"known: qa"):
            cmd_init(ctx, "test")

    def test_an_invalid_name_is_refused(
        self, make_context: Callable[..., Context]
    ) -> None:
        with pytest.raises(VbError, match="not a valid site name"):
            cmd_init(make_context(FakeRunner()), "../x")


OLD_KEY_FILE = "operator_key"
NEW_KEY_FILE = "generated_key"


def key_probe(
    *,
    new_denied_for: tuple[str, ...] = (),
    old_works_at_end: bool = False,
) -> Callable[[list[str]], RunResult]:
    """Answer logins by key: the old key works at first, the new one as set."""
    state = {"setup_runs": 0}

    def answer(argv: list[str]) -> RunResult:
        key = Path(argv[argv.index("-i") + 1]).name
        user = next(part.split("@")[0] for part in argv if "@" in part)
        if key == NEW_KEY_FILE:
            ok = user not in new_denied_for and state["setup_runs"] >= 1
        else:
            ok = state["setup_runs"] < 2 or old_works_at_end
        return RunResult(0, "", "") if ok else RunResult(255, "", "Permission denied")

    answer.state = state  # type: ignore[attr-defined]
    return answer


class RotationScenario:
    """Builds a runner and records what the files looked like during each setup."""

    def __init__(
        self,
        ctx: Context,
        *,
        setup_codes: tuple[int, int] = (0, 0),
        new_denied_for: tuple[str, ...] = (),
        old_works_at_end: bool = False,
    ) -> None:
        self.ctx = ctx
        self.site = ctx.sites_dir / "production"
        self.snapshots: list[dict[str, object]] = []
        probe = key_probe(
            new_denied_for=new_denied_for, old_works_at_end=old_works_at_end
        )
        runner = keygen_and_vault_runner()
        runner.capture_rules = [
            *build_runner().capture_rules[:2],
            (is_program("ssh-keygen", "-y"), RunResult(0, OLD_PUBLIC + "\n", "")),
            *runner.capture_rules,
            (login_probe("admin"), probe),
            (login_probe("service"), probe),
        ]
        codes = iter(setup_codes)

        def setup(argv: list[str]) -> int:
            probe.state["setup_runs"] += 1  # type: ignore[attr-defined]
            self.snapshots.append(
                {
                    "keys": read_operator_keys(
                        (self.site / "inventory.ini").read_text()
                    ),
                    "vault": (self.site / "operator_key.vault").read_text(),
                    "key_file": Path(argv[argv.index("--private-key") + 1]).name,
                    "user": argv[argv.index("-u") + 1],
                }
            )
            return next(codes)

        runner.interactive_rule = setup
        self.runner = runner
        ctx.runner = runner


class TestRotateKey:
    def test_the_key_is_replaced_in_two_steps(
        self,
        make_context: Callable[..., Context],
        messages: Messages,
        workspace_base: Path,
    ) -> None:
        ctx = make_context(build_runner())
        scenario = RotationScenario(ctx)
        assert cmd_rotate_key(ctx, "production") == 0
        first, second = scenario.snapshots
        assert first["keys"] == [OLD_PUBLIC, OTHER_PUBLIC, NEW_PUBLIC]
        assert (first["key_file"], first["user"]) == (OLD_KEY_FILE, "admin")
        assert first["vault"] == "$ANSIBLE_VAULT;1.1;AES256\n"
        assert second["keys"] == [OTHER_PUBLIC, NEW_PUBLIC]
        assert (second["key_file"], second["vault"]) == (NEW_KEY_FILE, "NEWENC")
        assert "Done" in messages.lines[-1]
        assert list(workspace_base.iterdir()) == []
        assert not (scenario.site / "operator_key.vault.new").exists()

    def test_a_failed_first_setup_keeps_the_old_key_stored(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner())
        scenario = RotationScenario(ctx, setup_codes=(5, 0))
        with pytest.raises(VbError, match="exit code 5"):
            cmd_rotate_key(ctx, "production")
        assert (
            scenario.site / "operator_key.vault"
        ).read_text() == "$ANSIBLE_VAULT;1.1;AES256\n"
        assert len(scenario.snapshots) == 1

    @pytest.mark.parametrize("denied", [("admin",), ("service",), ("admin", "service")])
    def test_a_new_key_that_does_not_log_in_everywhere_is_never_stored(
        self, make_context: Callable[..., Context], denied: tuple[str, ...]
    ) -> None:
        ctx = make_context(build_runner())
        scenario = RotationScenario(ctx, new_denied_for=denied)
        with pytest.raises(VbError, match="does not accept the operator key"):
            cmd_rotate_key(ctx, "production")
        assert (
            scenario.site / "operator_key.vault"
        ).read_text() == "$ANSIBLE_VAULT;1.1;AES256\n"
        assert len(scenario.snapshots) == 1

    def test_an_old_key_that_still_works_is_reported(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner())
        RotationScenario(ctx, old_works_at_end=True)
        with pytest.raises(VbError, match="old key still logs in"):
            cmd_rotate_key(ctx, "production")

    def test_the_second_setup_failing_is_reported(
        self, make_context: Callable[..., Context]
    ) -> None:
        ctx = make_context(build_runner())
        RotationScenario(ctx, setup_codes=(0, 7))
        with pytest.raises(VbError, match="exit code 7"):
            cmd_rotate_key(ctx, "production")

    def test_nothing_changes_when_the_current_key_does_not_work(
        self, make_context: Callable[..., Context]
    ) -> None:
        runner = build_runner(logins={"admin": RunResult(255, "", "Permission denied")})
        ctx = make_context(runner)
        before = (ctx.sites_dir / "production" / "inventory.ini").read_text()
        with pytest.raises(VbError, match="does not accept"):
            cmd_rotate_key(ctx, "production")
        assert (ctx.sites_dir / "production" / "inventory.ini").read_text() == before


TOOLS = ("ansible-vault", "ssh-keygen")


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS),
    reason="needs ansible-vault and ssh-keygen",
)
def test_init_with_the_real_tools_creates_a_key_that_only_the_password_opens(
    tmp_path: Path,
) -> None:
    sites, deploy = tmp_path / "sites", tmp_path / "deploy"
    sites.mkdir()
    make_template(deploy)
    lines: list[str] = []
    ctx = Context(
        sites_dir=sites,
        deploy_dir=deploy,
        runner=SubprocessRunner(),
        prompt_secret=lambda _prompt: PASSWORD,
        emit=lines.append,
        workspace_base=tmp_path,
    )
    assert cmd_init(ctx, "qa") == 0
    site = sites / "qa"
    vault = site / "operator_key.vault"
    assert vault.read_bytes().startswith(b"$ANSIBLE_VAULT;1.1;AES256")
    password_file = tmp_path / "pw"
    password_file.write_text(PASSWORD + "\n")
    private = subprocess.run(
        [
            "ansible-vault",
            "decrypt",
            "--vault-password-file",
            str(password_file),
            "--output",
            "-",
            str(vault),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    key_file = tmp_path / "key"
    key_file.write_text(private)
    key_file.chmod(0o600)
    derived = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(key_file)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[:2]
    listed = read_operator_keys((site / "inventory.ini").read_text())
    assert [key.split()[:2] for key in listed] == [derived]
    assert "CHANGEME.example.invalid" in (site / "inventory.ini").read_text()
    assert [
        path.name for path in tmp_path.iterdir() if path.name.startswith("vb-")
    ] == []


def test_the_fake_key_constant_is_a_private_key_block() -> None:
    assert FAKE_KEY.startswith("-----BEGIN")
