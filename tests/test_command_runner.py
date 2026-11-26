import dataclasses
import os
import subprocess
from unittest.mock import Mock

import pytest

from network_diagnostic.core import command_runner
from network_diagnostic.core.exceptions import (
    DiagnosticError, CommandError, CommandNotFoundError,
    CommandTimeoutError, CommandExecutionError, ValidationError,
    CommandUntrustedError, CommandPermissionError, CommandStartError, CommandOutputLimitError,
)
from network_diagnostic.core.models import CommandResult
from network_diagnostic.core.validation import MAX_ARGUMENT_LENGTH, MAX_ARGUMENTS


@pytest.fixture(autouse=True)
def monotonic_clock(monkeypatch):
    clock = Mock(side_effect=[100.0, 100.25])
    monkeypatch.setattr(command_runner.time, "monotonic", clock)
    return clock


EXPECTED_ENVIRONMENT = {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "LC_ALL": "C", "LANG": "C"}


def test_command_environment_is_built_from_an_allowlist_not_copied(monkeypatch):
    # Changed in Phase 14C.0: the environment is no longer a copy of os.environ.
    monkeypatch.setenv("LC_ALL", "fi_FI.UTF-8")
    monkeypatch.setenv("LANG", "fi_FI.UTF-8")
    monkeypatch.setenv("PATH", "/home/user/bin:.")
    monkeypatch.setenv("COMMAND_RUNNER_TEST", "not forwarded")
    original = os.environ.copy()

    result = command_runner.command_environment()
    result["COMMAND_RUNNER_TEST"] = "changed"

    assert result == {**EXPECTED_ENVIRONMENT, "COMMAND_RUNNER_TEST": "changed"}
    assert command_runner.command_environment() == EXPECTED_ENVIRONMENT
    assert dict(os.environ) == original


def test_command_environment_adds_missing_locale(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)

    result = command_runner.command_environment()

    assert result == EXPECTED_ENVIRONMENT
    assert "LC_ALL" not in os.environ


@pytest.mark.parametrize("name, value", [
    ("LD_PRELOAD", "/tmp/evil.so"), ("LD_LIBRARY_PATH", "/tmp/evil"), ("PYTHONPATH", "/tmp/evil"),
    ("PYTHONSTARTUP", "/tmp/evil.py"), ("IFS", "/"), ("BASH_ENV", "/tmp/evil.sh"), ("ENV", "/tmp/evil.sh"),
    ("HOSTALIASES", "/tmp/aliases"), ("RES_OPTIONS", "timeout:1"), ("LOCALDOMAIN", "evil.example"),
    ("HOME", "/tmp/home"), ("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/tmp/evil"), ("GCONV_PATH", "/tmp/evil"),
    ("LOCPATH", "/tmp/evil"), ("GLIBC_TUNABLES", "glibc.malloc.check=3"), ("POSIXLY_CORRECT", "1"),
])
def test_command_environment_does_not_forward_hostile_variables(name, value, monkeypatch):
    monkeypatch.setenv(name, value)

    result = command_runner.command_environment()

    assert name not in result
    assert result == EXPECTED_ENVIRONMENT


def test_run_command_returns_structured_result_and_safe_execution_options(
    monkeypatch, subprocess_run, monotonic_clock,
):
    arguments = ["hostname"]
    environment = {"LC_ALL": "C", "PATH": "/usr/bin"}
    policy = command_runner.COMMAND_POLICIES["hostname"]
    prepare_environment = Mock(return_value=environment)
    monkeypatch.setattr(command_runner, "command_environment", prepare_environment)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(arguments, 0, "host\n", "notice\n")

    result = command_runner.run_command(arguments)
    arguments.append("changed")

    assert isinstance(result, CommandResult)
    assert result == CommandResult(["hostname"], "host\n", "notice\n", 0, 0.25)
    prepare_environment.assert_called_once_with()
    # Changed in Phase 14C.0: the execution seam is _run_bounded; it receives the
    # resolved executable and the output limits, and decodes the output itself.
    subprocess_run.assert_called_once_with(
        ["hostname"], executable="/usr/bin/hostname", timeout=10, env=environment,
        max_stdout_bytes=policy.max_stdout_bytes, max_stderr_bytes=command_runner.MAX_STDERR_BYTES,
        max_total_bytes=command_runner.MAX_TOTAL_OUTPUT_BYTES,
    )
    assert monotonic_clock.call_count == 2


def test_run_command_uses_custom_timeout(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "", "")

    # Changed in Phase 14C.0: dig accepts its host only as the value of -q.
    result = command_runner.run_command(["dig", "-q", "example.com"], timeout=2.5)

    assert result == CommandResult(["dig", "-q", "example.com"], "", "", 0, 0.25)
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
    (PermissionError("Permission denied"), CommandPermissionError,
     "Could not execute nmcli: Permission denied"),
    (OSError("Too many open files"), CommandStartError,
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


@pytest.mark.parametrize("error_type", [
    CommandNotFoundError, CommandTimeoutError, CommandExecutionError,
    CommandUntrustedError, CommandPermissionError, CommandStartError, CommandOutputLimitError,
])
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


@pytest.mark.parametrize("arguments", ["hostname", [], [1], [""], None, ("hostname",)])
def test_run_command_rejects_invalid_argument_lists_before_execution(arguments, subprocess_run):
    with pytest.raises(ValidationError, match="arguments must be a non-empty list"):
        command_runner.run_command(arguments)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("timeout", [None, 0, -1, float("inf"), float("nan"), "10", True, False, 10 ** 400])
def test_run_command_rejects_missing_or_invalid_timeout(timeout, subprocess_run):
    with pytest.raises(ValidationError, match="timeout must be"):
        command_runner.run_command(["hostname"], timeout=timeout)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("arguments", [["hostname\0"], ["ping", "-c", "3", "127.0.0.1\0"]])
def test_run_command_rejects_nul_characters_before_execution(arguments, subprocess_run):
    with pytest.raises(ValidationError, match="arguments must not contain NUL"):
        command_runner.run_command(arguments)

    subprocess_run.assert_not_called()


def test_run_command_validation_error_is_not_a_command_error(subprocess_run):
    with pytest.raises(ValidationError) as caught:
        command_runner.run_command([""])

    assert isinstance(caught.value, DiagnosticError)
    assert not isinstance(caught.value, CommandError)
    subprocess_run.assert_not_called()


def test_run_command_allows_empty_later_arguments_and_spaces(monkeypatch, subprocess_run):
    # Changed in Phase 14C.0: printf is not on the allowlist, so a test policy is
    # added. General validation still accepts empty later arguments and spaces.
    policy = command_runner.CommandPolicy(
        max_timeout=10, max_stdout_bytes=1024, fixed_arguments=frozenset({""}),
        numeric_flags=frozenset(), operand_validator=lambda value: None,
    )
    monkeypatch.setitem(command_runner.COMMAND_POLICIES, "printf", policy)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")

    result = command_runner.run_command(["printf", "", "two words"])

    assert result.arguments == ["printf", "", "two words"]
    assert subprocess_run.call_args.args[0] == ["printf", "", "two words"]


def test_shared_fixture_blocks_unconfigured_command_execution(subprocess_run):
    with pytest.raises(AssertionError, match="Real system commands are forbidden"):
        command_runner.run_command(["hostname"])

    subprocess_run.assert_called_once()


@pytest.mark.parametrize("timeout", [60.000001, 61, 1e7, 10 ** 8, 1e300])
def test_run_command_rejects_timeout_above_maximum_before_execution(timeout, subprocess_run):
    with pytest.raises(ValidationError, match="timeout must be"):
        command_runner.run_command(["hostname"], timeout=timeout)

    subprocess_run.assert_not_called()


def test_run_command_accepts_maximum_timeout(monkeypatch, subprocess_run):
    # Changed in Phase 14C.0: hostname is limited to 10 s by its policy, so the
    # global 60 s boundary is exercised with a policy that allows it.
    policy = command_runner.COMMAND_POLICIES["hostname"]
    monkeypatch.setitem(command_runner.COMMAND_POLICIES, "hostname", dataclasses.replace(policy, max_timeout=60))
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "host", "")

    command_runner.run_command(["hostname"], timeout=60)

    assert subprocess_run.call_args.kwargs["timeout"] == 60


def test_run_command_replaces_undecodable_output_instead_of_raising(subprocess_run):
    # Phase 13 regression: text mode decoded output strictly with the locale
    # encoding, so one invalid byte (for example, in /etc/hosts aliases or a
    # connection name) raised UnicodeDecodeError past every DiagnosticError
    # handler. Changed in Phase 14C.0: decoding moved into _run_bounded (UTF-8,
    # errors="replace", covered with real processes in
    # test_command_runner_processes.py). Here the fake decodes like _run_bounded
    # and run_command must pass the replaced text through unchanged.
    def decode_like_run_bounded(arguments, **options):
        assert "encoding" not in options and "errors" not in options and "text" not in options
        return subprocess.CompletedProcess(
            arguments, 0,
            b"192.0.2.10 host \xff\n".decode("utf-8", "replace"), b"warn \xfe\n".decode("utf-8", "replace"),
        )

    subprocess_run.side_effect = decode_like_run_bounded

    result = command_runner.run_command(["getent", "hosts", "example.com"])

    assert result.stdout == "192.0.2.10 host \ufffd\n"
    assert result.stderr == "warn \ufffd\n"


# ---------------------------------------------------------------------------
# Phase 14C.0: command allowlist, argument grammar and policy limits
# ---------------------------------------------------------------------------

def completed(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_command_policies_pin_the_allowlist_and_limits():
    # Changing this table is a security decision and needs Tech Lead approval.
    expected = {
        "ip": (10, 3 * 1024 * 1024), "ss": (10, 3 * 1024 * 1024), "nmcli": (10, 1024 * 1024),
        "hostname": (10, 64 * 1024), "ping": (15, 256 * 1024), "getent": (15, 256 * 1024),
        "dig": (15, 256 * 1024),
    }

    policies = command_runner.COMMAND_POLICIES

    assert {name: (p.max_timeout, p.max_stdout_bytes) for name, p in policies.items()} == expected
    assert command_runner.MAX_STDERR_BYTES == 64 * 1024
    assert command_runner.MAX_TOTAL_OUTPUT_BYTES == 4 * 1024 * 1024
    assert command_runner.TRUSTED_DIRECTORIES == ("/usr/bin", "/usr/sbin", "/bin", "/sbin")
    assert MAX_ARGUMENTS == 32
    assert MAX_ARGUMENT_LENGTH == 512
    for policy in policies.values():
        assert policy.max_stdout_bytes + command_runner.MAX_STDERR_BYTES < command_runner.MAX_TOTAL_OUTPUT_BYTES


@pytest.mark.parametrize("name", ["sudo", "pkexec", "su", "doas", "sh", "bash", "env", "python3", "printf"])
def test_privilege_escalation_and_shell_commands_are_never_allowlisted(name, subprocess_run):
    assert name not in command_runner.COMMAND_POLICIES

    with pytest.raises(ValidationError, match="Command is not allowed"):
        command_runner.run_command([name, "ip", "-4", "route"])

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("name", [
    "/bin/ip", "/usr/bin/ping", "../ip", "./ip", "bin/ip", "ip ", " ip", "IP", "-ip", "+ip", "@ip", "ip\\", "~/ip",
])
def test_command_must_be_an_exact_bare_allowlist_name(name, monkeypatch, subprocess_run):
    resolver = Mock(side_effect=AssertionError("Resolver must not run for rejected commands"))
    monkeypatch.setattr(command_runner, "_resolve_executable", resolver)

    with pytest.raises(ValidationError, match="Command is not allowed"):
        command_runner.run_command([name, "-4", "route"])

    resolver.assert_not_called()
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("arguments, allowed_codes", [
    (["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], (0,)),
    (["nmcli", "-t", "-e", "yes", "-f", "DEVICE,STATE", "device", "status"], (0,)),
    (["ss", "-H", "-tuln"], (0,)),
    (["ip", "-4", "route"], (0,)),
    (["ip", "-4", "-o", "addr", "show", "dev", "enp3s0"], (0,)),
    (["hostname"], (0,)),
    (["getent", "hosts", "example.com"], (0, 2)),
    (["getent", "ahostsv4", "example.com"], (0, 2)),
    (["getent", "ahostsv6", "2001:db8::1"], (0, 2)),
    (["dig", "-r", "+time=3", "+tries=1", "+stats", "-q", "example.com"], (0, 9)),
    (["ping", "-n", "-c", "3", "-W", "2", "192.0.2.10"], (0, 1)),
    (["ping", "-n", "-c", "3", "-W", "2", "example.com"], (0, 1)),
])
def test_current_callers_argument_lists_pass_the_grammar(arguments, allowed_codes, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(allowed_codes[0])

    result = command_runner.run_command(list(arguments), allowed_codes=allowed_codes)

    assert result.arguments == arguments
    assert subprocess_run.call_args.args[0] == arguments
    assert subprocess_run.call_args.kwargs["executable"] == f"/usr/bin/{arguments[0]}"


@pytest.mark.parametrize("arguments", [
    # Option injection through the free operand.
    ["ping", "-n", "-c", "3", "-W", "2", "-x"],
    ["ping", "-n", "-c", "3", "-W", "2", "-f"],
    ["ping", "-n", "-c", "3", "-W", "2", "+x"],
    ["ping", "-n", "-c", "3", "-W", "2", "@192.0.2.1"],
    ["ping", "-x"],
    ["getent", "hosts", "-x"],
    ["getent", "hosts", "+x"],
    ["getent", "hosts", "@192.0.2.1"],
    ["dig", "-r", "+time=3", "+tries=1", "+stats", "-q", "-x"],
    ["dig", "-q", "@192.0.2.1"],
    ["ip", "-4", "-o", "addr", "show", "dev", "-x"],
    ["ip", "-4", "-o", "addr", "show", "dev", "eth0;id"],
    # Unknown flags, sub-commands and databases.
    ["ping", "-s", "100", "example.com"],
    ["ping", "-n", "-c", "3", "-W", "2", "-I", "eth0", "example.com"],
    ["getent", "passwd", "root"],
    ["getent", "-s", "dns", "hosts", "example.com"],
    ["dig", "+trace", "example.com"],
    ["dig", "-f", "/etc/passwd"],
    ["dig", "-k", "keyfile", "-q", "example.com"],
    ["ip", "link", "set", "dev", "eth0", "down"],
    ["ip", "-4", "route", "add", "192.0.2.0/24"],
    ["ip", "-4", "route", "eth0"],
    ["ss", "-p"],
    ["ss", "-H", "-tuln", "extra"],
    ["nmcli", "connection", "show", "-s"],
    ["nmcli", "device", "wifi", "connect", "ssid"],
    ["hostname", "-I"],
    ["hostname", "newname"],
    # Numbers only after a numeric flag, one to three digits.
    ["ping", "3", "example.com"],
    ["ping", "-n", "-c", "1000", "-W", "2", "example.com"],
    ["ping", "-n", "-c", "-3", "-W", "2", "example.com"],
    ["ping", "-n", "-c", "3x", "-W", "2", "example.com"],
    ["ping", "-n", "-c", "", "-W", "2", "example.com"],
    ["ping", "-n", "-c", "3", "-W", "2", "5", "example.com"],
])
def test_grammar_rejects_unknown_tokens_and_option_like_operands(arguments, subprocess_run):
    with pytest.raises(ValidationError):
        command_runner.run_command(arguments, allowed_codes=(0, 1, 2, 9))

    subprocess_run.assert_not_called()


def test_grammar_accepts_numeric_values_only_after_numeric_flags(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed()

    command_runner.run_command(["ping", "-n", "-c", "1", "-W", "999", "example.com"], allowed_codes=(0, 1))

    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "1", "-W", "999", "example.com"]


@pytest.mark.parametrize("arguments", [
    ["getent", "hosts", "example.com;id"],
    ["getent", "hosts", "example.com && reboot"],
    ["getent", "hosts", "$(id)"],
    ["getent", "hosts", "`id`"],
    ["getent", "hosts", "example.com|cat"],
    ["getent", "hosts", "example.com > /tmp/x"],
    ["ping", "-n", "-c", "3", "-W", "2", "example.com\nid"],
    ["ping", "-n", "-c", "3", "-W", "2", "127.0.0.1 -f"],
    ["dig", "-r", "+time=3", "+tries=1", "+stats", "-q", "example.com +trace"],
])
def test_shell_metacharacters_and_embedded_options_never_reach_execution(arguments, subprocess_run):
    with pytest.raises(ValidationError):
        command_runner.run_command(arguments, allowed_codes=(0, 1, 2, 9))

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("bad", ["\n", "\t", "\r", "\x01", "\x1b", "\x1f", "\x7f"])
def test_run_command_rejects_control_characters_in_any_argument(bad, subprocess_run):
    with pytest.raises(ValidationError, match="control characters"):
        command_runner.run_command(["getent", "hosts", f"example{bad}.com"])
    with pytest.raises(ValidationError, match="control characters"):
        command_runner.run_command([f"hostname{bad}"])

    subprocess_run.assert_not_called()


def test_run_command_enforces_argument_count_boundary(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed()
    limit = MAX_ARGUMENTS

    command_runner.run_command(["ping"] + ["-n"] * (limit - 1), allowed_codes=(0, 1))
    with pytest.raises(ValidationError):
        command_runner.run_command(["ping"] + ["-n"] * limit, allowed_codes=(0, 1))

    subprocess_run.assert_called_once()


def test_run_command_rejects_overlong_argument(subprocess_run):
    with pytest.raises(ValidationError):
        command_runner.run_command(["getent", "hosts", "a" * (MAX_ARGUMENT_LENGTH + 1)])

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("command, maximum", [
    ("ip", 10), ("ss", 10), ("nmcli", 10), ("hostname", 10), ("ping", 15), ("getent", 15), ("dig", 15),
])
def test_timeout_above_the_command_policy_is_rejected_and_the_limit_is_accepted(command, maximum, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed()

    with pytest.raises(ValidationError):
        command_runner.run_command([command], timeout=maximum + 0.5)
    subprocess_run.assert_not_called()

    command_runner.run_command([command], timeout=maximum)

    assert subprocess_run.call_args.kwargs["timeout"] == maximum


@pytest.mark.parametrize("allowed_codes", [[0], (), "0", None, 0, (True,), (False, 0), (0, "1"), (0, 1.0), (None,)])
def test_run_command_rejects_invalid_allowed_codes_before_execution(allowed_codes, subprocess_run):
    with pytest.raises(ValidationError):
        command_runner.run_command(["hostname"], allowed_codes=allowed_codes)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("name", ["ip", "ss", "nmcli", "hostname", "ping", "getent", "dig"])
def test_run_command_passes_policy_limits_for_each_command(name, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed()
    policy = command_runner.COMMAND_POLICIES[name]

    command_runner.run_command([name])

    kwargs = subprocess_run.call_args.kwargs
    assert kwargs["max_stdout_bytes"] == policy.max_stdout_bytes
    assert kwargs["max_stderr_bytes"] == command_runner.MAX_STDERR_BYTES
    assert kwargs["max_total_bytes"] == command_runner.MAX_TOTAL_OUTPUT_BYTES


# ---------------------------------------------------------------------------
# Phase 14C.0: error contracts, error context and message bounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    CommandNotFoundError("Required command not found: ip", reason="not found in trusted directories"),
    CommandUntrustedError("Refusing to run ip: world-writable", reason="world-writable"),
    CommandPermissionError("Could not execute ip: not executable", reason="not executable"),
])
def test_resolution_errors_are_completed_with_call_context_without_executing(error, monkeypatch, subprocess_run):
    monkeypatch.setattr(command_runner, "_resolve_executable", Mock(side_effect=error))
    arguments = ["ip", "-4", "route"]

    with pytest.raises(type(error)) as caught:
        command_runner.run_command(arguments, timeout=7)

    assert caught.value is error
    assert error.arguments == ["ip", "-4", "route"]
    assert error.arguments is not arguments
    assert error.timeout == 7
    subprocess_run.assert_not_called()


def test_untrusted_command_is_a_not_found_error_and_permission_is_not():
    assert issubclass(CommandUntrustedError, CommandNotFoundError)
    assert issubclass(CommandPermissionError, CommandExecutionError)
    assert not issubclass(CommandPermissionError, CommandNotFoundError)
    for error_type in (CommandPermissionError, CommandStartError, CommandOutputLimitError):
        assert issubclass(error_type, CommandExecutionError)
        assert not issubclass(error_type, CommandTimeoutError)
    assert not issubclass(CommandUntrustedError, CommandExecutionError)


def test_output_limit_error_from_the_seam_is_completed_with_call_context(subprocess_run):
    error = CommandOutputLimitError("ip output limit", reason="stdout exceeded 10 bytes", stdout="partial")
    subprocess_run.side_effect = error

    with pytest.raises(CommandOutputLimitError) as caught:
        command_runner.run_command(["ip", "-4", "route"], timeout=6)

    assert caught.value is error
    assert error.arguments == ["ip", "-4", "route"]
    assert error.timeout == 6
    assert error.stdout == "partial"
    assert error.return_code is None


def test_run_command_does_not_map_unexpected_exceptions(subprocess_run):
    subprocess_run.side_effect = RuntimeError("programming error")

    with pytest.raises(RuntimeError, match="programming error"):
        command_runner.run_command(["hostname"])


def test_run_command_does_not_resolve_commands_through_the_environment_path(monkeypatch, subprocess_run):
    monkeypatch.setenv("PATH", "/tmp/evil:.")
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed()

    command_runner.run_command(["hostname"])

    assert subprocess_run.call_args.kwargs["executable"] == "/usr/bin/hostname"
    assert "/tmp/evil" not in subprocess_run.call_args.kwargs["env"]["PATH"]


@pytest.mark.parametrize("stderr_text, detail", [
    ("a" * 200, "a" * 200),
    ("  " + "a" * 200 + "\n", "a" * 200),
    ("a" * 201, "a" * 200 + "..."),
    ("b" * 5000, "b" * 200 + "..."),
    ("\n  \nfirst line\nsecond line\n", "first line"),
    ("  Permission denied\n", "Permission denied"),
    ("\x1b[31mred\x1b[0m\n", "\\x1b[31mred\\x1b[0m"),
    ("evil \u202e txt\n", "evil \\u202e txt"),
], ids=["exact", "padded", "one-over", "long", "multiline", "strip", "escape-sequence", "bidi"])
def test_error_detail_is_the_first_non_empty_stderr_line_escaped_and_limited(stderr_text, detail, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(2, "out", stderr_text)

    with pytest.raises(CommandExecutionError) as caught:
        command_runner.run_command(["ip", "-4", "route"])

    error = caught.value
    assert command_runner.MAX_ERROR_DETAIL_CHARS == 200
    assert str(error) == f"ip failed: {detail}"
    assert error.reason == detail
    assert error.stderr == stderr_text


@pytest.mark.parametrize("stderr_text", ["", " \n\t\n"])
def test_error_detail_falls_back_to_the_exit_code_for_blank_stderr(stderr_text, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(5, "", stderr_text)

    with pytest.raises(CommandExecutionError, match="^ip failed: exit code 5$") as caught:
        command_runner.run_command(["ip", "-4", "route"])

    assert caught.value.reason == "exit code 5"


@pytest.mark.parametrize("original, error_type", [
    (subprocess.SubprocessError("bad"), CommandStartError),
    (FileNotFoundError(2, "No such file or directory", "/"), CommandNotFoundError),
])
def test_start_errors_without_oserror_type_and_missing_files_are_mapped(original, error_type, subprocess_run):
    subprocess_run.side_effect = original

    with pytest.raises(error_type) as caught:
        command_runner.run_command(["nmcli"])

    assert caught.value.__cause__ is original
    assert caught.value.return_code is None


@pytest.mark.parametrize("arguments", [
    ["ping", "-n", "-c", "0", "-W", "2", "example.com"],
    ["ping", "-n", "-c", "3", "-W", "0", "example.com"],
    ["ping", "-n", "-c", "03", "-W", "2", "example.com"],
    ["dig", "-q", "-r"],
])
def test_grammar_rejects_zero_counts_and_flags_used_as_operand_values(arguments, subprocess_run):
    with pytest.raises(ValidationError):
        command_runner.run_command(arguments, allowed_codes=(0, 1, 9))

    subprocess_run.assert_not_called()


def test_terminate_group_still_sends_sigkill_when_the_grace_wait_is_interrupted(monkeypatch):
    killpg = Mock()
    monkeypatch.setattr(command_runner.os, "killpg", killpg)
    process = Mock(pid=4242)
    process.wait.side_effect = [KeyboardInterrupt, None]

    with pytest.raises(KeyboardInterrupt):
        command_runner._terminate_group(process)

    assert [call.args[1] for call in killpg.call_args_list] == [
        command_runner.signal.SIGTERM, command_runner.signal.SIGKILL,
    ]



# ---------------------------------------------------------------------------
# Phase 14C.0: guard meta tests
# ---------------------------------------------------------------------------

def test_shared_guard_blocks_real_processes_and_process_group_signals():
    with pytest.raises(AssertionError, match="Real processes are forbidden"):
        command_runner.subprocess.Popen(["hostname"])
    with pytest.raises(AssertionError, match="forbidden"):
        command_runner.subprocess.run(["hostname"])
    with pytest.raises(AssertionError, match="forbidden"):
        command_runner.os.killpg(os.getpgrp(), 0)


def test_shared_guard_resolves_commands_without_touching_the_file_system():
    assert command_runner._resolve_executable("nmcli") == "/usr/bin/nmcli"


# ---------------------------------------------------------------------------
# Phase 14C.0: process group cleanup helpers (mocked signals, no processes)
# ---------------------------------------------------------------------------

@pytest.fixture
def group_signals(monkeypatch):
    killpg = Mock()
    monkeypatch.setattr(command_runner.os, "killpg", killpg)
    return killpg


def test_terminate_group_sends_sigterm_then_always_sigkill_to_the_group(group_signals):
    process = Mock(pid=4242)
    process.wait.side_effect = [None, None]

    command_runner._terminate_group(process)

    assert group_signals.call_args_list == [
        ((4242, command_runner.signal.SIGTERM),), ((4242, command_runner.signal.SIGKILL),),
    ]
    assert process.wait.call_args_list[0].kwargs == {"timeout": command_runner.TERMINATE_GRACE_SECONDS}
    process.kill.assert_not_called()


def test_terminate_group_escalates_after_the_grace_period_and_ignores_empty_groups(group_signals):
    process = Mock(pid=4242)
    process.wait.side_effect = [subprocess.TimeoutExpired("x", 1), None]
    group_signals.side_effect = ProcessLookupError

    command_runner._terminate_group(process)

    assert group_signals.call_count == 2


@pytest.mark.parametrize("pid", [0, 1, -1])
def test_terminate_group_never_signals_group_zero_or_one(pid, group_signals):
    process = Mock(pid=pid)

    command_runner._terminate_group(process)

    group_signals.assert_not_called()
    process.kill.assert_called_once_with()


def test_terminate_group_falls_back_to_killing_the_process_when_signalling_is_not_permitted(group_signals):
    process = Mock(pid=4242)
    process.wait.return_value = 0
    group_signals.side_effect = PermissionError
    process.kill.side_effect = [None, ProcessLookupError()]

    command_runner._terminate_group(process)

    assert process.kill.call_count == 2


def test_terminate_group_hands_an_unkillable_process_to_a_daemon_reaper_thread(group_signals, monkeypatch):
    process = Mock(pid=4242)
    process.wait.side_effect = subprocess.TimeoutExpired("x", 1)
    thread = Mock()
    thread_class = Mock(return_value=thread)
    monkeypatch.setattr(command_runner.threading, "Thread", thread_class)

    command_runner._terminate_group(process)

    thread_class.assert_called_once_with(target=process.wait, daemon=True)
    thread.start.assert_called_once_with()


def test_not_allowed_command_message_shows_only_a_bounded_prefix_of_the_name(subprocess_run):
    with pytest.raises(ValidationError, match="Command is not allowed") as caught:
        command_runner.run_command(["x" * 400])

    assert len(str(caught.value)) < 200
    subprocess_run.assert_not_called()


def test_policy_with_an_operand_marker_requires_an_operand_validator():
    with pytest.raises(ValueError, match="operand_validator"):
        command_runner.CommandPolicy(max_timeout=10, max_stdout_bytes=1, fixed_arguments=frozenset(), operand_after="-q")
