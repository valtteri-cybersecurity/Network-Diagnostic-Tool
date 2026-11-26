from unittest.mock import Mock

import pytest

import command_runner
import output


@pytest.fixture(autouse=True)
def subprocess_run(monkeypatch):
    # All command-based diagnostics use this same subprocess module.
    command = Mock(side_effect=AssertionError("Real system commands are forbidden"))
    monkeypatch.setattr(command_runner.subprocess, "run", command)
    return command


@pytest.fixture(autouse=True)
def system_command(monkeypatch):
    command = Mock(side_effect=AssertionError("Real shell commands are forbidden"))
    monkeypatch.setattr(output.os, "system", command)
    return command
