from unittest.mock import Mock

import pytest

from network_diagnostic.core import command_runner
from network_diagnostic.cli import output


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_processes: run real local processes (no network); removes the command runner guard",
    )


@pytest.fixture(autouse=True)
def subprocess_run(request, monkeypatch):
    """Guard against real commands in every test that is not marked real_processes.

    The fixture keeps its historical name: it returns the mock that replaces
    the runner's execution seam (command_runner._run_bounded).
    """
    if request.node.get_closest_marker("real_processes"):
        return None
    command = Mock(side_effect=AssertionError("Real system commands are forbidden"))
    monkeypatch.setattr(command_runner, "_run_bounded", command)
    monkeypatch.setattr(command_runner, "_resolve_executable", lambda name: f"/usr/bin/{name}")
    # Block every other way of starting a process or signalling a process group.
    monkeypatch.setattr(
        command_runner.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("Real processes are forbidden")),
    )
    monkeypatch.setattr(
        command_runner.subprocess,
        "run",
        Mock(side_effect=AssertionError("Real system commands are forbidden")),
    )
    monkeypatch.setattr(
        command_runner.os,
        "killpg",
        Mock(side_effect=AssertionError("Real process group signals are forbidden")),
    )
    return command


@pytest.fixture(autouse=True)
def system_command(monkeypatch):
    command = Mock(side_effect=AssertionError("Real shell commands are forbidden"))
    monkeypatch.setattr(output.os, "system", command)
    return command
