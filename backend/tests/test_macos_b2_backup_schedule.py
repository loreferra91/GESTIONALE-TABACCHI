"""Test opt-in LaunchAgent creation without installing a schedule."""
import os
import plistlib
import stat

import pytest

from scripts.install_macos_b2_backup_schedule import make_launch_agent


def _paths(tmp_path):
    runner = tmp_path / "run.sh"
    runner.write_text("#!/bin/bash\nexit 0\n")
    config = tmp_path / "private.env"
    config.write_text("B2_BUCKET='synthetic'\n")
    config.chmod(0o600)
    return runner, config


def test_macos_scheduler_only_generates_a_safe_agent(tmp_path):
    runner, config = _paths(tmp_path)
    doc = make_launch_agent(
        shell_runner=runner, config=config, log_dir=tmp_path / "logs",
        hour=2, minute=30,
    )
    assert doc["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    assert doc["RunAtLoad"] is False
    assert doc["ProgramArguments"] == ["/bin/bash", str(runner)]
    assert doc["EnvironmentVariables"]["GESTIONALE_BACKUP_ENV"] == str(config)
    assert "secret" not in str(doc).lower()
    assert not (tmp_path / "logs").exists()
    assert plistlib.loads(plistlib.dumps(doc)) == doc


def test_schedule_blocks_world_readable_config(tmp_path):
    runner, config = _paths(tmp_path)
    config.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        make_launch_agent(shell_runner=runner, config=config, log_dir=tmp_path)


def test_schedule_blocks_symlinks(tmp_path):
    runner, config = _paths(tmp_path)
    link = tmp_path / "linked.env"
    link.symlink_to(config)
    with pytest.raises(ValueError, match="configuration"):
        make_launch_agent(shell_runner=runner, config=link, log_dir=tmp_path)


@pytest.mark.parametrize("h,m", [(-1,30),(24,30),(2,-1),(2,60)])
def test_schedule_rejects_invalid_hours(tmp_path,h,m):
    runner, config = _paths(tmp_path)
    with pytest.raises(ValueError, match="schedule"):
        make_launch_agent(shell_runner=runner, config=config, log_dir=tmp_path, hour=h,minute=m)
