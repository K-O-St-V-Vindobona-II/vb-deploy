"""Shared test helpers: a scripted runner and a context built on a temp tree."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from vb_tools.context import Context
from vb_tools.runner import RunResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PASSWORD = "correct-horse-battery-staple-1234"
FAKE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
)
OLD_PUBLIC = "ssh-ed25519 AAAAOLDKEY operator-old"
OTHER_PUBLIC = "ssh-ed25519 AAAAOTHERKEY colleague"
NEW_PUBLIC = "ssh-ed25519 AAAANEWKEY operator-new"
INVENTORY_JSON = json.dumps(
    {"_meta": {"hostvars": {"web.example.org": {"ansible_host": "203.0.113.7"}}}}
)
INVENTORY_TEXT = (
    "[vps]\nweb.example.org\n\n[vps:vars]\napp_environment=production\n"
    f"operator_public_keys={json.dumps([OLD_PUBLIC, OTHER_PUBLIC])}\n"
)

OK = RunResult(0, "", "")
DENIED = RunResult(255, "", "user@host: Permission denied (publickey).")

Outcome = RunResult | Callable[[list[str]], RunResult]
Matcher = Callable[[list[str]], bool]


@dataclass
class FakeRunner:
    """Answers captured runs from rules and records every call."""

    capture_rules: list[tuple[Matcher, Outcome]] = field(default_factory=list)
    interactive_rule: Callable[[list[str]], int] = lambda _argv: 0
    calls: list[tuple[str, list[str], Path | None]] = field(default_factory=list)

    def capture(self, argv: Sequence[str], cwd: Path | None = None) -> RunResult:
        arguments = list(argv)
        self.calls.append(("capture", arguments, cwd))
        for matches, outcome in self.capture_rules:
            if matches(arguments):
                return outcome(arguments) if callable(outcome) else outcome
        message = f"unexpected capture call: {arguments}"
        raise AssertionError(message)

    def interactive(self, argv: Sequence[str], cwd: Path | None = None) -> int:
        arguments = list(argv)
        self.calls.append(("interactive", arguments, cwd))
        return self.interactive_rule(arguments)

    def interactive_calls(self) -> list[list[str]]:
        return [argv for kind, argv, _ in self.calls if kind == "interactive"]


def is_program(name: str, *words: str) -> Matcher:
    """Match a call by program name and by words that occur in its arguments."""
    return lambda argv: argv[0] == name and all(word in argv for word in words)


def login_probe(user: str) -> Matcher:
    """Match the BatchMode probe for one user."""
    return lambda argv: (
        argv[0] == "ssh"
        and "BatchMode=yes" in argv
        and any(part.startswith(f"{user}@") for part in argv)
    )


def vault_decrypt_rule(text: str = FAKE_KEY) -> tuple[Matcher, Outcome]:
    return is_program("ansible-vault", "decrypt"), RunResult(0, text, "")


def build_runner(*, logins: dict[str, RunResult] | None = None) -> FakeRunner:
    """A runner that decrypts the key, lists one host and answers login probes."""
    runner = FakeRunner()
    runner.capture_rules = [
        vault_decrypt_rule(),
        (is_program("ansible-inventory"), RunResult(0, INVENTORY_JSON, "")),
        (is_program("ssh-keygen", "-y"), RunResult(0, OLD_PUBLIC + "\n", "")),
    ]
    for user, result in (
        logins or {"admin": OK, "service": OK, "root": DENIED}
    ).items():
        runner.capture_rules.append((login_probe(user), result))
    return runner


@dataclass
class Messages:
    lines: list[str] = field(default_factory=list)

    def __call__(self, text: str) -> None:
        self.lines.append(text)


@pytest.fixture
def messages() -> Messages:
    return Messages()


@pytest.fixture
def workspace_base(tmp_path: Path) -> Path:
    base = tmp_path / "ram"
    base.mkdir()
    return base


@pytest.fixture
def make_context(
    tmp_path: Path, workspace_base: Path, messages: Messages
) -> Callable[..., Context]:
    """Build a Context on a temp tree with one site 'production'."""

    def build(
        runner: FakeRunner,
        *,
        password: str = PASSWORD,
        missing_tools: tuple[str, ...] = (),
    ) -> Context:
        sites_dir = tmp_path / "sites"
        site = sites_dir / "production"
        site.mkdir(parents=True, exist_ok=True)
        (site / "inventory.ini").write_text(INVENTORY_TEXT, encoding="utf-8")
        (site / "operator_key.vault").write_text("$ANSIBLE_VAULT;1.1;AES256\n")
        deploy = tmp_path / "deploy"
        deploy.mkdir(exist_ok=True)
        return Context(
            sites_dir=sites_dir,
            deploy_dir=deploy,
            runner=runner,
            prompt_secret=lambda _prompt: password,
            emit=messages,
            which=lambda name: None if name in missing_tools else f"/usr/bin/{name}",
            workspace_base=workspace_base,
        )

    return build
