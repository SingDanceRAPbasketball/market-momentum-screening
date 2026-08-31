from pathlib import Path

from market_momentum.service_manager import SERVICE_LABEL, build_launch_agent, launch_agent_path


def test_build_launch_agent_keeps_local_service_alive(tmp_path: Path):
    project = tmp_path / "project"
    executable = project / ".venv" / "bin" / "market-momentum"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    payload = build_launch_agent(project, port=9876)

    assert payload["Label"] == SERVICE_LABEL
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProgramArguments"][-1] == "9876"
    assert payload["EnvironmentVariables"]["MARKET_MOMENTUM_MANAGED"] == "launchd"
    assert "/opt/homebrew/bin" in payload["EnvironmentVariables"]["PATH"]


def test_launch_agent_path_uses_user_launch_agents(tmp_path: Path):
    assert launch_agent_path(tmp_path) == (
        tmp_path.resolve() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
    )
