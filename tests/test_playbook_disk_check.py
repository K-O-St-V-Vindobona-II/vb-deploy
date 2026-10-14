"""The free-disk check that deploy.yml runs before it creates a Garage mirror.

The task file is played for real against the local machine: the decision lives
in Jinja conditions, which only a run of Ansible can prove.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

TASK_FILE = (
    Path(__file__).resolve().parent.parent
    / "playbooks"
    / "tasks"
    / ("require_free_disk.yml")
)
PLAYBOOK = f"""---
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_tasks: {TASK_FILE}
"""
HUGE_REQUIREMENT_GIB = 10**9

ANSIBLE_BIN = Path(shutil.which("ansible-playbook") or "/nonexistent").parent

pytestmark = pytest.mark.skipif(
    shutil.which("ansible-playbook") is None,
    reason="ansible-playbook is not installed",
)


@dataclass(frozen=True)
class Outcome:
    """The result of one play of the task file."""

    returncode: int
    output: str


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A stand-in for the home directory of the user 'service'."""
    directory = tmp_path / "home"
    directory.mkdir()
    return directory


def play(
    tmp_path: Path,
    home: Path,
    *,
    stage: str,
    min_free_gib: int | None,
    check: bool = False,
    in_inventory: bool = False,
) -> Outcome:
    """Play the task file; min_free_gib is passed with -e, or set the way a
    site does it, in the inventory, or left out for the default."""
    playbook = tmp_path / "play.yml"
    playbook.write_text(PLAYBOOK)
    inventory = tmp_path / "inventory.ini"
    host_vars = f" min_free_disk_gb={min_free_gib}" if in_inventory else ""
    inventory.write_text(f"localhost ansible_connection=local{host_vars}\n")
    command = [
        "ansible-playbook",
        "-i",
        str(inventory),
        str(playbook),
        "-e",
        f"app_environment={stage}",
        "-e",
        f"ansible_user_dir={home}",
    ]
    if min_free_gib is not None and not in_inventory:
        command += ["-e", f"min_free_disk_gb={min_free_gib}"]
    if check:
        command.append("--check")
    result = subprocess.run(  # noqa: PLW1510 - the return code is asserted
        command,
        cwd=tmp_path,
        env={
            "PATH": f"{ANSIBLE_BIN}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "ANSIBLE_HOME": str(tmp_path / "ansible"),
            "ANSIBLE_LOCALHOST_WARNING": "False",
            "ANSIBLE_NOCOLOR": "True",
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    return Outcome(result.returncode, result.stdout + result.stderr)


def garage_data(home: Path, stage: str) -> Path:
    return home / "data" / "vb" / stage / "garage-data"


class TestFreeDiskCheck:
    def test_a_disk_that_is_too_small_stops_the_first_deploy(
        self, tmp_path: Path, home: Path
    ) -> None:
        outcome = play(tmp_path, home, stage="qa", min_free_gib=HUGE_REQUIREMENT_GIB)
        assert outcome.returncode != 0
        assert "GiB are free" in outcome.output
        assert "min_free_disk_gb=0" in outcome.output

    def test_enough_free_space_lets_the_deploy_go_on(
        self, tmp_path: Path, home: Path
    ) -> None:
        outcome = play(tmp_path, home, stage="test", min_free_gib=1)
        assert outcome.returncode == 0, outcome.output

    def test_zero_switches_the_check_off(self, tmp_path: Path, home: Path) -> None:
        outcome = play(tmp_path, home, stage="test", min_free_gib=0)
        assert outcome.returncode == 0, outcome.output

    def test_an_existing_mirror_is_not_checked_again(
        self, tmp_path: Path, home: Path
    ) -> None:
        garage_data(home, "qa").mkdir(parents=True)
        outcome = play(tmp_path, home, stage="qa", min_free_gib=HUGE_REQUIREMENT_GIB)
        assert outcome.returncode == 0, outcome.output

    def test_production_is_never_checked(self, tmp_path: Path, home: Path) -> None:
        outcome = play(
            tmp_path, home, stage="production", min_free_gib=HUGE_REQUIREMENT_GIB
        )
        assert outcome.returncode == 0, outcome.output

    def test_the_preview_reports_the_real_free_space(
        self, tmp_path: Path, home: Path
    ) -> None:
        outcome = play(
            tmp_path, home, stage="qa", min_free_gib=HUGE_REQUIREMENT_GIB, check=True
        )
        assert outcome.returncode != 0
        assert "GiB are free" in outcome.output

    def test_the_check_is_bound_to_the_stage_of_the_directory(
        self, tmp_path: Path, home: Path
    ) -> None:
        garage_data(home, "test").mkdir(parents=True)
        outcome = play(tmp_path, home, stage="qa", min_free_gib=HUGE_REQUIREMENT_GIB)
        assert outcome.returncode != 0

    def test_a_value_in_the_inventory_is_honoured(
        self, tmp_path: Path, home: Path
    ) -> None:
        too_much = play(
            tmp_path,
            home,
            stage="qa",
            min_free_gib=HUGE_REQUIREMENT_GIB,
            in_inventory=True,
        )
        switched_off = play(
            tmp_path, home, stage="qa", min_free_gib=0, in_inventory=True
        )

        assert too_much.returncode != 0
        assert f"at least {HUGE_REQUIREMENT_GIB} GiB" in too_much.output
        assert switched_off.returncode == 0, switched_off.output

    def test_without_a_value_the_default_is_60_gib(
        self, tmp_path: Path, home: Path
    ) -> None:
        outcome = play(tmp_path, home, stage="qa", min_free_gib=None)

        # Whether this machine has 60 GiB free is not the point: either way the
        # output names the default as the requirement.
        expected = "required: 60 GiB" if outcome.returncode == 0 else "at least 60 GiB"
        assert expected in outcome.output


def test_deploy_yml_sets_no_default_that_would_override_the_inventory() -> None:
    """A play variable beats the inventory in Ansible, so the documented switch
    in the inventory would silently do nothing."""
    deploy = (TASK_FILE.parent.parent / "deploy.yml").read_text()

    assert not re.search(r"^\s*min_free_disk_gb\s*:", deploy, re.MULTILINE)
