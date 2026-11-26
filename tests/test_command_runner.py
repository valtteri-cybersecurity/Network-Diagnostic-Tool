import os
import subprocess
from unittest.mock import Mock

import pytest

import command_runner
from exceptions import (
    DiagnosticError, CommandError, CommandNotFoundError,
    CommandTimeoutError, CommandExecutionError,
)
from models import CommandResult


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch):
    clock = Mock(side_effect=[100.0, 100.25])
    monkeypatch.setattr(command_runner.time, "monotonic", clock)
    return clock


def test_command_environment_copies_variables_and_overrides_locale(monkeypatch):
    monkeypatch.setenv("LC_ALL", "fi_FI.UTF-8")
    monkeypatch.setenv("COMMAND_RUNNER_TEST", "preserved")
    original = os.environ.copy()

    result = command_runner.command_environment()
    result["COMMAND_RUNNER_TEST"] = "changed"

    assert result == {**original, "LC_ALL": "C", "COMMAND_RUNNER_TEST": "changed"}
    assert dict(os.environ) == original


def test_command_environment_adds_missing_locale(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)

    result = command_runner.command_environment()

    assert result["LC_ALL"] == "C"
    assert "LC_ALL" not in os.environ


def test_run_command_returns_structured_result_and_safe_execution_options(
    monkeypatch, subprocess_run, monotonic_clock,
):
    arguments = ["hostname"]
    environment = {"LC_ALL": "C", "PATH": "/usr/bin"}
    prepare_environment = Mock(return_value=environment)
    monkeypatch.setattr(command_runner, "command_environment", prepare_environment)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(arguments, 0, "host\n", "notice\n")

    result = command_runner.run_command(arguments)
    arguments.append("changed")

    assert isinstance(result, CommandResult)
    assert result == CommandResult(["hostname"], "host\n", "notice\n", 0, 0.25)
    prepare_environment.assert_called_once_with()
    subprocess_run.assert_called_once_with(
        ["hostname"], capture_output=True, text=True, timeout=10,
        env=environment, shell=False, check=False,
    )
    assert monotonic_clock.call_count == 2


def test_run_command_uses_custom_timeout(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "", "")

    result = command_runner.run_command(["dig", "example.com"], timeout=2.5)

    assert result == CommandResult(["dig", "example.com"], "", "", 0, 0.25)
    assert subprocess_run.call_args.kwargs["timeout"] == 2.5


@pytest.mark.parametrize("code", [0, 1, 2])
def test_run_command_accepts_configured_return_codes(code, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], code, "", "notice")

    result = command_runner.run_command(["getent"], allowed_codes=(0, 1, 2))

    assert result == CommandResult(["getent"], "", "notice", code, 0.25)


@pytest.mark.parametrize("code, stderr, reason", [
    (2, "  Permission denied\n", "Permission denied"),
    (1, "", "exit code 1"),
    (2, " \n", "exit code 2"),
    (-15, "", "exit code -15"),
])
def test_run_command_rejects_error_codes_even_with_stdout(code, stderr, reason, subprocess_run):
    arguments = ["ip", "-4", "route"]
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(arguments, code, "partial output\n", stderr)

    with pytest.raises(CommandExecutionError) as caught:
        command_runner.run_command(arguments)

    error = caught.value
    assert str(error) == f"ip failed: {reason}"
    assert error.arguments == arguments
    assert error.reason == reason
    assert error.return_code == code
    assert error.stdout == "partial output\n"
    assert error.stderr == stderr
    assert error.timeout == 10
    assert error.__cause__ is None


def test_run_command_rejects_zero_when_not_allowed(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "", "")

    with pytest.raises(CommandExecutionError, match="^getent failed: exit code 0$"):
        command_runner.run_command(["getent"], allowed_codes=(2,))


@pytest.mark.parametrize("original, error_type, message", [
    (FileNotFoundError("missing"), CommandNotFoundError, "Required command not found: nmcli"),
    (PermissionError("Permission denied"), CommandExecutionError,
     "Could not execute nmcli: Permission denied"),
    (OSError("Too many open files"), CommandExecutionError,
     "Could not execute nmcli: Too many open files"),
])
def test_run_command_wraps_os_errors_with_context(original, error_type, message, subprocess_run):
    subprocess_run.side_effect = original

    with pytest.raises(error_type) as caught:
        command_runner.run_command(["nmcli"], timeout=3)

    error = caught.value
    assert str(error) == message
    assert error.arguments == ["nmcli"]
    assert error.reason == str(original)
    assert error.return_code is None
    assert error.stdout == ""
    assert error.stderr == ""
    assert error.timeout == 3
    assert error.__cause__ is original
    subprocess_run.assert_called_once()


@pytest.mark.parametrize("stdout, stderr, expected_stdout, expected_stderr", [
    (None, None, "", ""),
    ("", "", "", ""),
    ("partial reply\n", "warning\n", "partial reply\n", "warning\n"),
    (b"partial reply\n", b"warning\n", "partial reply\n", "warning\n"),
    (b"bad \xff\n", b"bad \xfe\n", "bad \ufffd\n", "bad \ufffd\n"),
    ("reply", b"warning", "reply", "warning"),
])
def test_run_command_preserves_timeout_output_as_text(
    stdout, stderr, expected_stdout, expected_stderr, subprocess_run,
):
    original = subprocess.TimeoutExpired(["ping", "example.com"], 4, output=stdout, stderr=stderr)
    subprocess_run.side_effect = original

    with pytest.raises(CommandTimeoutError) as caught:
        command_runner.run_command(["ping", "example.com"], timeout=4)

    error = caught.value
    assert str(error) == "ping timed out after 4 seconds."
    assert error.arguments == ["ping", "example.com"]
    assert error.reason == str(original)
    assert error.return_code is None
    assert error.stdout == expected_stdout
    assert error.stderr == expected_stderr
    assert error.timeout == 4
    assert error.__cause__ is original


@pytest.mark.parametrize("error_type", [CommandNotFoundError, CommandTimeoutError, CommandExecutionError])
def test_command_exceptions_inherit_shared_base_and_copy_arguments(error_type):
    arguments = ["ping", "example.com"]

    error = error_type("failed", arguments=arguments)
    arguments.append("changed")

    assert isinstance(error, CommandError)
    assert isinstance(error, DiagnosticError)
    assert isinstance(error, Exception)
    assert error.arguments == ["ping", "example.com"]
    assert error.reason == "failed"
    assert str(error) == "failed"


def test_command_error_can_be_created_with_message_only():
    error = CommandError("command failed")

    assert error.arguments == []
    assert error.reason == "command failed"
    assert error.return_code is None
    assert error.stdout == ""
    assert error.stderr == ""
    assert error.timeout is None


@pytest.mark.parametrize("arguments", ["hostname", [], [1], [""], None])
def test_run_command_rejects_invalid_argument_lists_before_execution(arguments, subprocess_run):
    with pytest.raises(ValueError, match="arguments must be"):
        command_runner.run_command(arguments)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("timeout", [None, 0, -1, float("inf"), float("nan"), "10"])
def test_run_command_rejects_missing_or_invalid_timeout(timeout, subprocess_run):
    with pytest.raises(ValueError, match="timeout must be"):
        command_runner.run_command(["hostname"], timeout=timeout)

    subprocess_run.assert_not_called()


def test_shared_fixture_blocks_unconfigured_command_execution(subprocess_run):
    with pytest.raises(AssertionError, match="Real system commands are forbidden"):
        command_runner.run_command(["hostname"])

    subprocess_run.assert_called_once()
