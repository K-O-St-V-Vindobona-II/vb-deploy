"""Guards for the documentation: both languages stay in step, every link and
anchor resolves, the anchors other repositories link to exist, and nothing of
the earlier access concept comes back."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vb_tools.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_PAIRS = [
    (ROOT / "README.md", ROOT / "README.de.md"),
    (ROOT / "HANDOVER.md", ROOT / "HANDOVER.de.md"),
]
ALL_DOCS = [path for pair in LANGUAGE_PAIRS for path in pair]

# Anchors the READMEs of vb-api, vb-intern and vb-www link to.
LINKED_FROM_OTHER_REPOSITORIES = {
    "README.md": [
        "14-containers-and-pods",
        "17-how-a-change-reaches-production",
        "24-the-database-is-damaged-backup-and-restore",
        "42-operational-scripts-in-vb-api",
        "57-local-development-environment",
        "appendix-a-secrets-and-required-variables",
    ],
    "README.de.md": [
        "14-container-und-pods",
        "17-wie-eine-änderung-in-produktion-kommt",
        "24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung",
        "42-betriebs-skripte-in-vb-api",
        "57-lokale-entwicklungsumgebung",
        "anhang-a-secrets-und-pflichtvariablen",
    ],
}

# Words of the earlier access concept that must not appear in documentation or tooling.
STALE_TERMS = [
    "extra_authorized_keys",
    "local_ssh_pub_key",
    "--ask-become-pass",
    "--ask-vault-pass",
    "ssh-copy-id",
    "admin_pw",
    "service_pw",
    "inventory/production.ini",
    "secrets/production",
    "credentials.yml",
    "credentials.txt",
    "emergency key",
    "emergency access set",
    "Notfallschlüssel",
    "Notfall-Zugangs-Set",
]
TOOLING_FILES = [
    *sorted((ROOT / "vb_tools").glob("*.py")),
    ROOT / "playbooks" / "setup_vps.yml",
    ROOT / "playbooks" / "deploy.yml",
    ROOT / "ansible.cfg",
]
KNOWN_COMMANDS = {"setup", "deploy", "check", "ssh", "init", "rotate-key"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def outside_code_blocks(text: str) -> list[str]:
    """The lines of a Markdown text that are not inside a fenced code block."""
    lines: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_block = not in_block
            continue
        if not in_block:
            lines.append(line)
    return lines


def headings(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for line in outside_code_blocks(read(path)):
        match = re.match(r"^(#{1,6}) (.+)$", line)
        if match:
            found.append((len(match.group(1)), match.group(2)))
    return found


def slug(heading: str) -> str:
    """The anchor GitHub makes from a heading."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(text) for _, text in headings(path)}


def code_block_count(path: Path) -> int:
    return (
        sum(1 for line in read(path).splitlines() if line.lstrip().startswith("```"))
        // 2
    )


def table_count(path: Path) -> int:
    count = 0
    previous_was_table = False
    for line in outside_code_blocks(read(path)):
        is_table = line.startswith("|")
        count += is_table and not previous_was_table
        previous_was_table = is_table
    return count


@pytest.mark.parametrize(("english", "german"), LANGUAGE_PAIRS)
class TestBothLanguages:
    def test_the_heading_levels_are_the_same(self, english: Path, german: Path) -> None:
        assert [level for level, _ in headings(english)] == [
            level for level, _ in headings(german)
        ]

    def test_the_number_of_code_blocks_and_tables_is_the_same(
        self, english: Path, german: Path
    ) -> None:
        assert code_block_count(english) == code_block_count(german)
        assert table_count(english) == table_count(german)

    def test_each_language_links_to_the_other(
        self, english: Path, german: Path
    ) -> None:
        assert f"[Deutsch]({german.name})" in read(english)
        assert f"[English]({english.name})" in read(german)


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda path: path.name)
class TestLinks:
    def test_links_within_the_file_resolve(self, path: Path) -> None:
        own = anchors(path)
        broken = [
            target
            for target in re.findall(r"\]\(#([^)]+)\)", read(path))
            if target not in own
        ]
        assert broken == []

    def test_links_to_a_neighbouring_document_resolve(self, path: Path) -> None:
        broken: list[str] = []
        for name, target in re.findall(
            r"\]\(([A-Za-z.]+\.md)(?:#([^)]*))?\)", read(path)
        ):
            neighbour = ROOT / name
            if not neighbour.is_file():
                broken.append(name)
            elif target and target not in anchors(neighbour):
                broken.append(f"{name}#{target}")
        assert broken == []


@pytest.mark.parametrize("name", sorted(LINKED_FROM_OTHER_REPOSITORIES))
def test_the_anchors_other_repositories_link_to_exist(name: str) -> None:
    missing = [
        anchor
        for anchor in LINKED_FROM_OTHER_REPOSITORIES[name]
        if anchor not in anchors(ROOT / name)
    ]
    assert missing == []


@pytest.mark.parametrize(
    "path", [*ALL_DOCS, *TOOLING_FILES], ids=lambda path: path.name
)
def test_nothing_of_the_earlier_access_concept_is_left(path: Path) -> None:
    text = read(path)
    assert [term for term in STALE_TERMS if term in text] == []


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda path: path.name)
def test_documented_vb_commands_exist(path: Path) -> None:
    used = set(re.findall(r"\./vb (?:--sites-dir \S+ )?([a-z][a-z-]*)", read(path)))
    parser_commands = set(
        build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    )
    assert used <= KNOWN_COMMANDS
    assert parser_commands == KNOWN_COMMANDS


VAULT_PASSWORD_VALUE = re.compile(
    r"(?i)vault[-_ ]?pass(?:word|wort)?\s*[:=]\s*[A-Za-z0-9_\-]{12,}\b"
)


def test_no_document_mentions_a_vault_password_value() -> None:
    assert [
        path.name for path in ALL_DOCS if VAULT_PASSWORD_VALUE.search(read(path))
    ] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vault password: correct-horse-battery-staple", True),
        ("vault_pass=abcdefghijklmnop", True),
        ("Vault-Passwort: Xk39sLmPq0aZ77", True),
        ("Change the Vault password: `ansible-vault rekey` on every file", False),
        ("das Vault-Passwort: **Nimm dasselbe**", False),
        ("The Vault password is the only secret", False),
    ],
)
def test_the_vault_password_heuristic_tells_values_from_prose(
    text: str, expected: bool
) -> None:
    assert bool(VAULT_PASSWORD_VALUE.search(text)) is expected
