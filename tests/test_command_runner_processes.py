"""Tests of the Command Runner with real local processes.

Every test starts only local Python (or sh/cat) processes. None uses the network,
privileges or a diagnostic tool. The marker removes the shared mock guard from
conftest.py, so these tests must stay small, bounded and local.
"""

import gc
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time

import pytest

from network_diagnostic.core import command_runner
from network_diagnostic.core.exceptions import (
    CommandExecutionError, CommandOutputLimitError, CommandPermissionError, CommandNotFoundError,
)

pytestmark = pytest.mark.real_processes

ENVIRONMENT = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
MIB = 1024 * 1024


def run_python(code, *, timeout=10, stdout=MIB, stderr=MIB, total=4 * MIB, env=None):
    """Run a Python snippet through the bounded runner (isolated mode, no site files)."""
    return command_runner._run_bounded(
        [sys.executable, "-I", "-c", code], executable=sys.executable, timeout=timeout,
        env=ENVIRONMENT if env is None else env, max_stdout_bytes=stdout, max_stderr_bytes=stderr,
        max_total_bytes=total,
    )


def process_state(pid):
    """Return the process state letter from /proc, or None when the process does not exist."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat_file:
            return stat_file.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


def wait_until_gone(pid, seconds=3.0):
    """Wait until a process is gone. A zombie counts as gone: only its parent can reap it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process_state(pid) in (None, "Z"):
            return True
        time.sleep(0.02)
    return process_state(pid) in (None, "Z")


def assert_group_gone(group_id):
    with pytest.raises(ProcessLookupError):
        os.killpg(group_id, 0)


def open_descriptors():
    gc.collect()
    return sorted(os.listdir("/proc/self/fd"))


class RecordingPopen(subprocess.Popen):
    """Popen that remembers every process it started."""

    started = []

    def __init__(self, *arguments, **options):
        super().__init__(*arguments, **options)
        RecordingPopen.started.append(self)


@pytest.fixture
def recorded_processes(monkeypatch):
    RecordingPopen.started = []
    monkeypatch.setattr(command_runner.subprocess, "Popen", RecordingPopen)
    return RecordingPopen.started


def test_marker_removes_the_mock_guard():
    assert isinstance(command_runner.subprocess.Popen, type)
    assert command_runner.subprocess.Popen is subprocess.Popen
    assert not hasattr(command_runner.os.killpg, "assert_called")
    assert not hasattr(command_runner._run_bounded, "assert_called")
    assert not hasattr(command_runner.subprocess.run, "assert_called")
    assert command_runner._resolve_executable.__name__ == "_resolve_executable"


# --- normal execution, decoding, exit codes -------------------------------

def test_normal_run_returns_text_output_and_exit_code():
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"

    result = run_python(code)

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.args == [sys.executable, "-I", "-c", code]
    assert (result.returncode, result.stdout, result.stderr) == (3, "out\n", "err\n")


def test_signal_death_is_reported_as_a_negative_return_code():
    result = run_python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")

    assert result.returncode == -signal.SIGKILL


def test_invalid_utf8_is_replaced_instead_of_raising():
    code = "import os; os.write(1, b'bad \\xff\\n'); os.write(2, b'warn \\xfe\\n')"

    result = run_python(code)

    assert result.stdout == "bad \ufffd\n"
    assert result.stderr == "warn \ufffd\n"


def test_multibyte_characters_survive_read_chunk_boundaries():
    text = "\u00e4" * 100_000  # 200 000 bytes, several 64 KiB chunks
    code = "import os; os.write(1, ('\\u00e4' * 100000).encode('utf-8'))"

    result = run_python(code, stdout=MIB)

    assert result.stdout == text
    assert "\ufffd" not in result.stdout


# --- execution context ------------------------------------------------------

def test_child_sees_closed_stdin_root_directory_own_session_and_exact_environment():
    code = (
        "import json, os, sys\n"
        "environ = open('/proc/self/environ', 'rb').read().split(b'\\0')\n"
        "print(json.dumps({'stdin': sys.stdin.read(), 'stdin_link': os.readlink('/proc/self/fd/0'),\n"
        "    'cwd': os.getcwd(), 'sid': os.getsid(0) == os.getpid(), 'pgid': os.getpgrp() == os.getpid(),\n"
        "    'environ': sorted(item.decode() for item in environ if item)}))\n"
    )
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}

    result = run_python(code, env=environment)

    seen = json.loads(result.stdout)
    assert seen["stdin"] == ""
    assert seen["stdin_link"] == "/dev/null"
    assert seen["cwd"] == "/"
    assert seen["sid"] is True
    assert seen["pgid"] is True
    assert seen["environ"] == sorted(f"{key}={value}" for key, value in environment.items())


def test_run_command_gives_the_child_only_the_allowlisted_environment(monkeypatch):
    # Acceptance criterion 26: hostile variables and HOME never reach the command.
    for name, value in {
        "LD_PRELOAD": "/nonexistent/evil.so", "PYTHONPATH": "/nonexistent", "HOSTALIASES": "/nonexistent",
        "HOME": "/nonexistent", "IFS": "x", "BASH_ENV": "/nonexistent", "RES_OPTIONS": "timeout:1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/nonexistent/evil:.")
    code = "import sys; sys.stdout.write(open('/proc/self/environ').read().replace(chr(0), chr(10)))"
    result = run_command_with_python(monkeypatch, code)

    assert sorted(result.stdout.split()) == sorted(
        f"{key}={value}" for key, value in command_runner.command_environment().items()
    )


def run_command_with_python(monkeypatch, code, **options):
    """Run a Python snippet through run_command by registering a test-only command."""
    policy = command_runner.CommandPolicy(
        max_timeout=15, max_stdout_bytes=64 * 1024, fixed_arguments=frozenset({"-I", "-c"}),
        operand_validator=lambda value: None,
    )
    monkeypatch.setitem(command_runner.COMMAND_POLICIES, "python-test", policy)
    monkeypatch.setattr(command_runner, "_resolve_executable", lambda name: sys.executable)
    return command_runner.run_command(["python-test", "-I", "-c", code], **options)


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("cat") is None, reason="needs sh and cat")
def test_sigpipe_is_restored_to_the_default_disposition_in_the_child():
    # The test process (Python) ignores SIGPIPE; the child must not inherit that.
    result = command_runner._run_bounded(
        ["sh", "-c", "cat /proc/self/status"], executable=shutil.which("sh"), timeout=10, env=ENVIRONMENT,
        max_stdout_bytes=MIB, max_stderr_bytes=MIB, max_total_bytes=4 * MIB,
    )

    ignored = next(line for line in result.stdout.splitlines() if line.startswith("SigIgn:"))
    assert not int(ignored.split()[1], 16) & (1 << (signal.SIGPIPE - 1))


# --- output limits ----------------------------------------------------------

def stream_writer(descriptor, size, *, then="pass"):
    return f"import os, time; os.write({descriptor}, b'x' * {size}); {then}"


@pytest.mark.parametrize("descriptor, stream", [(1, "stdout"), (2, "stderr")])
def test_stream_limit_allows_exactly_the_limit(descriptor, stream):
    limits = {"stdout": 1000, "stderr": 1000}

    result = run_python(stream_writer(descriptor, 1000), **limits)

    assert len(getattr(result, stream)) == 1000


@pytest.mark.parametrize("descriptor, stream", [(1, "stdout"), (2, "stderr")])
def test_stream_limit_stops_the_command_one_byte_over(descriptor, stream, recorded_processes):
    code = stream_writer(descriptor, 1001, then="time.sleep(30)")

    started = time.monotonic()
    with pytest.raises(CommandOutputLimitError) as caught:
        run_python(code, stdout=1000, stderr=1000)

    error = caught.value
    assert time.monotonic() - started < 5
    assert stream in error.reason
    assert len(getattr(error, stream)) == 1000
    assert len(error.stdout) <= 1000 and len(error.stderr) <= 1000
    assert error.return_code is None
    assert recorded_processes[0].poll() is not None
    assert_group_gone(recorded_processes[0].pid)


def test_total_limit_applies_when_both_streams_are_within_their_own_limits():
    code = "import os; os.write(1, b'x' * 100); os.write(2, b'y' * 100)"

    assert len(run_python(code, stdout=100, stderr=100, total=200).stdout) == 100
    with pytest.raises(CommandOutputLimitError) as caught:
        run_python(code, stdout=100, stderr=100, total=199)

    assert "total" in caught.value.reason
    assert len(caught.value.stdout) + len(caught.value.stderr) <= 199


def test_total_limit_applies_when_both_streams_grow_at_the_same_time():
    code = (
        "import os, time\n"
        "while True:\n"
        "    os.write(1, b'o' * 10); os.write(2, b'e' * 10); time.sleep(0.001)\n"
    )

    with pytest.raises(CommandOutputLimitError) as caught:
        run_python(code, stdout=MIB, stderr=MIB, total=5000, timeout=10)

    error = caught.value
    assert "total" in error.reason
    assert len(error.stdout) + len(error.stderr) <= 5000
    assert error.stdout and error.stderr


def test_flooding_command_is_stopped_early_and_only_bounded_output_is_kept():
    code = "import os\nchunk = b'x' * MIB\nfor _ in range(50):\n    os.write(1, chunk)\n".replace("MIB", str(MIB))

    started = time.monotonic()
    with pytest.raises(CommandOutputLimitError) as caught:
        run_python(code, stdout=1024, timeout=20)

    assert time.monotonic() - started < 5
    assert len(caught.value.stdout) <= 1024


@pytest.mark.parametrize("first, second", [(2, 1), (1, 2)], ids=["stderr-first", "stdout-first"])
def test_full_pipes_do_not_deadlock_when_one_stream_is_written_before_the_other(first, second):
    code = f"import os; os.write({first}, b'a' * {MIB}); os.write({second}, b'b' * {MIB})"

    started = time.monotonic()
    result = run_python(code, stdout=2 * MIB, stderr=2 * MIB, total=4 * MIB, timeout=10)

    assert time.monotonic() - started < 8
    assert len(result.stdout if second == 1 else result.stderr) == MIB
    assert len(result.stderr if second == 1 else result.stdout) == MIB


# --- timeouts and descendants ----------------------------------------------

def test_timeout_stops_a_sleeping_command_and_keeps_bounded_partial_output(recorded_processes):
    code = "import sys, time; print('partial', flush=True); print('warn', file=sys.stderr, flush=True); time.sleep(30)"

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_python(code, timeout=0.7)

    assert time.monotonic() - started < 0.7 + 2.5
    assert caught.value.timeout == 0.7
    assert caught.value.output == b"partial\n"
    assert caught.value.stderr == b"warn\n"
    assert recorded_processes[0].poll() is not None
    assert_group_gone(recorded_processes[0].pid)


def test_timeout_kills_descendants_that_ignore_sigterm():
    grandchild = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    code = (
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"child = subprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_python(code, timeout=1.0)

    grandchild_pid = int(caught.value.output.split()[0])
    assert wait_until_gone(grandchild_pid)


def test_timeout_is_enforced_while_the_command_keeps_producing_output():
    code = "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.05)\n"

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_python(code, timeout=0.8)

    assert time.monotonic() - started < 0.8 + 2.5
    assert caught.value.output.startswith(b"tick\n")


def test_orphaned_pipe_holder_does_not_block_the_result_and_is_stopped():
    grandchild = "import time; time.sleep(60)"
    code = (
        "import subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-I', '-c', {grandchild!r}])\n"
        "print(child.pid, flush=True)\n"
        "sys.exit(7)\n"
    )

    started = time.monotonic()
    result = run_python(code, timeout=10)

    assert time.monotonic() - started < 4
    assert result.returncode == 7
    assert wait_until_gone(int(result.stdout.split()[0]))


# --- cleanup on exceptions and descriptors ---------------------------------

class InterruptingSelector(selectors.DefaultSelector):
    error = KeyboardInterrupt

    def select(self, timeout=None):
        raise self.error


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_interrupt_in_the_read_loop_stops_the_process_group_and_closes_the_pipes(
    error, monkeypatch, recorded_processes,
):
    InterruptingSelector.error = error
    monkeypatch.setattr(command_runner.selectors, "DefaultSelector", InterruptingSelector)
    before = open_descriptors()

    with pytest.raises(error):
        run_python("import time; time.sleep(30)", timeout=10)

    process = recorded_processes[0]
    assert process.poll() is not None
    assert_group_gone(process.pid)
    assert process.stdout.closed and process.stderr.closed
    assert open_descriptors() == before


def after_success():
    return run_python("print('ok')")


def after_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        run_python("import time; time.sleep(30)", timeout=0.5)


def after_limit():
    with pytest.raises(CommandOutputLimitError):
        run_python(stream_writer(1, 2000, then="time.sleep(30)"), stdout=1000)


def after_missing_executable():
    with pytest.raises(FileNotFoundError):
        command_runner._run_bounded(
            ["missing"], executable="/nonexistent/missing", timeout=5, env=ENVIRONMENT,
            max_stdout_bytes=1000, max_stderr_bytes=1000, max_total_bytes=2000,
        )


def after_permission_error():
    # The file is a directory, which exec refuses with PermissionError (EACCES).
    with pytest.raises(PermissionError):
        command_runner._run_bounded(
            ["tmp"], executable="/tmp", timeout=5, env=ENVIRONMENT,
            max_stdout_bytes=1000, max_stderr_bytes=1000, max_total_bytes=2000,
        )


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
@pytest.mark.parametrize("scenario", [
    after_success, after_timeout, after_limit, after_missing_executable, after_permission_error,
], ids=lambda function: function.__name__)
def test_no_file_descriptor_leaks_on_any_path(scenario):
    scenario()  # warm-up: lazily opened descriptors must not count as leaks
    before = open_descriptors()

    scenario()

    assert open_descriptors() == before


# --- run_command on real processes -----------------------------------------

def test_run_command_returns_a_structured_result_for_a_real_process(monkeypatch):
    result = run_command_with_python(monkeypatch, "import sys; print('hello'); print('w', file=sys.stderr)")

    assert result.stdout == "hello\n"
    assert result.stderr == "w\n"
    assert result.return_code == 0
    assert 0 <= result.duration_seconds < 10
    assert result.arguments[0] == "python-test"


def test_run_command_maps_real_exit_codes_and_signal_death(monkeypatch):
    with pytest.raises(CommandExecutionError) as exit_error:
        run_command_with_python(monkeypatch, "import sys; print('so'); print('boom', file=sys.stderr); sys.exit(4)")
    with pytest.raises(CommandExecutionError) as signal_error:
        run_command_with_python(monkeypatch, "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")

    assert str(exit_error.value) == "python-test failed: boom"
    assert (exit_error.value.return_code, exit_error.value.stdout) == (4, "so\n")
    assert signal_error.value.return_code == -signal.SIGKILL


def test_run_command_maps_real_timeout_and_output_limit(monkeypatch):
    with pytest.raises(command_runner.CommandTimeoutError) as timeout_error:
        run_command_with_python(monkeypatch, "import time; print('p', flush=True); time.sleep(30)", timeout=0.6)
    with pytest.raises(CommandOutputLimitError) as limit_error:
        run_command_with_python(
            monkeypatch, "import os, time; os.write(1, b'x' * 70000); time.sleep(30)", timeout=10,
        )

    assert timeout_error.value.stdout == "p\n"
    assert timeout_error.value.timeout == 0.6
    assert limit_error.value.arguments[0] == "python-test"
    assert limit_error.value.timeout == 10
    assert len(limit_error.value.stdout) <= 64 * 1024


def test_run_command_maps_real_start_errors(monkeypatch):
    policy = command_runner.CommandPolicy(
        max_timeout=10, max_stdout_bytes=1024, fixed_arguments=frozenset(),
    )
    monkeypatch.setitem(command_runner.COMMAND_POLICIES, "missing-test", policy)
    monkeypatch.setattr(command_runner, "_resolve_executable", lambda name: "/nonexistent/missing")
    with pytest.raises(CommandNotFoundError):
        command_runner.run_command(["missing-test"])

    monkeypatch.setattr(command_runner, "_resolve_executable", lambda name: "/tmp")
    with pytest.raises(CommandPermissionError) as caught:
        command_runner.run_command(["missing-test"])

    assert caught.value.return_code is None
    assert caught.value.arguments == ["missing-test"]


def test_timeout_also_covers_the_wait_for_a_command_that_closed_its_pipes(recorded_processes):
    code = "import os, time; os.close(1); os.close(2); time.sleep(30)"

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_python(code, timeout=0.6)

    assert time.monotonic() - started < 0.6 + 2.5
    assert_group_gone(recorded_processes[0].pid)


def test_failure_to_create_the_selector_still_stops_the_process_and_closes_the_pipes(
    monkeypatch, recorded_processes,
):
    def broken_selector():
        raise OSError("no selector available")

    monkeypatch.setattr(command_runner.selectors, "DefaultSelector", broken_selector)
    before = open_descriptors()

    with pytest.raises(OSError, match="no selector available"):
        run_python("import time; time.sleep(30)")

    process = recorded_processes[0]
    assert process.poll() is not None
    assert_group_gone(process.pid)
    assert process.stdout.closed and process.stderr.closed
    assert open_descriptors() == before


def test_default_resolver_uses_the_trusted_directories_and_only_root_as_owner(monkeypatch):
    # No process is started; the marker is needed because conftest.py replaces _resolve_executable.
    seen = []

    def fake_resolution(name, directories, **options):
        seen.append((name, directories, options))
        return "/trusted/" + name

    monkeypatch.setattr(command_runner, "resolve_trusted_executable", fake_resolution)

    assert command_runner._resolve_executable("ping") == "/trusted/ping"
    assert seen == [("ping", ("/usr/bin", "/usr/sbin", "/bin", "/sbin"), {"trusted_uids": (0,)})]
