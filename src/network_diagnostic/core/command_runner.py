"""Hardened execution of diagnostic commands.

Only commands in COMMAND_POLICIES can run. Each command is resolved to a real
file in a trusted system directory, started without a shell in its own session
with a minimal environment, and read with hard limits on time and output size.
"""

import os
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from network_diagnostic.core.exceptions import (
    CommandError, CommandExecutionError, CommandNotFoundError, CommandOutputLimitError,
    CommandPermissionError, CommandStartError, CommandTimeoutError, CommandUntrustedError,
    ValidationError,
)
from network_diagnostic.core.models import CommandResult
from network_diagnostic.core.validation import (
    validate_command_arguments, validate_host, validate_interface_name, validate_timeout,
)


# Directories searched for commands, in order. $PATH is never read. /usr/local and
# /opt are left out on purpose: a locally installed program must not replace a
# system tool without a deliberate change here.
TRUSTED_DIRECTORIES = ("/usr/bin", "/usr/sbin", "/bin", "/sbin")
# Only files and directories owned by root are trusted. There is no setting for this.
TRUSTED_OWNER_UIDS = (0,)
MAX_SYMLINKS = 16

MAX_STDERR_BYTES = 64 * 1024
# Hard memory ceiling for stdout and stderr together.
MAX_TOTAL_OUTPUT_BYTES = 4 * 1024 * 1024

# Seconds between SIGTERM and SIGKILL for the process group of a stopped command.
TERMINATE_GRACE_SECONDS = 1.0
# Seconds to wait for the main process after SIGKILL before a reaper thread takes over.
REAP_WAIT_SECONDS = 5.0
# Seconds to keep reading after the main process has exited but a pipe is still open.
DRAIN_SECONDS = 0.5
READ_CHUNK_BYTES = 65536
POLL_SLICE_SECONDS = 0.2

READ_DONE = "done"
READ_ORPHANED = "orphaned"
READ_TIMEOUT = "timeout"
READ_LIMIT = "limit"

MAX_ERROR_DETAIL_CHARS = 200
TRUNCATED_MARKER = "..."

# 1-999: zero is rejected because some ping versions treat 0 as "no limit".
_NUMERIC_VALUE = re.compile(r"[1-9][0-9]{0,2}")


@dataclass(frozen=True)
class CommandPolicy:
    """What one allowed command may be asked to do.

    max_timeout: largest timeout in seconds that a caller may request.
    max_stdout_bytes: stdout limit; reaching more stops the command.
    fixed_arguments: exact tokens (flags and keywords) that are allowed.
    numeric_flags: flags that may be followed by a number from 1 to 999.
    operand_validator: validates the one free operand; None means no free operand.
    operand_after: when set, the token after this one is the operand and is always
        validated; when None, the last token is the operand if it is not a fixed token.
    """

    max_timeout: float
    max_stdout_bytes: int
    fixed_arguments: frozenset
    numeric_flags: frozenset = frozenset()
    operand_validator: Optional[Callable[[str], None]] = None
    operand_after: Optional[str] = None

    def __post_init__(self):
        if self.operand_after is not None and self.operand_validator is None:
            raise ValueError("operand_after requires an operand_validator")


COMMAND_POLICIES = {
    "ip": CommandPolicy(
        max_timeout=10, max_stdout_bytes=3 * 1024 * 1024,
        fixed_arguments=frozenset({"-4", "-o", "route", "addr", "show", "dev"}),
        operand_validator=validate_interface_name, operand_after="dev",
    ),
    "ss": CommandPolicy(
        max_timeout=10, max_stdout_bytes=3 * 1024 * 1024,
        fixed_arguments=frozenset({"-H", "-tuln"}),
    ),
    "nmcli": CommandPolicy(
        max_timeout=10, max_stdout_bytes=1024 * 1024,
        fixed_arguments=frozenset({
            "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "DEVICE,STATE",
            "device", "status",
        }),
    ),
    "hostname": CommandPolicy(
        max_timeout=10, max_stdout_bytes=64 * 1024, fixed_arguments=frozenset(),
    ),
    "ping": CommandPolicy(
        max_timeout=15, max_stdout_bytes=256 * 1024,
        fixed_arguments=frozenset({"-n", "-c", "-W"}),
        numeric_flags=frozenset({"-c", "-W"}), operand_validator=validate_host,
    ),
    "getent": CommandPolicy(
        max_timeout=15, max_stdout_bytes=256 * 1024,
        fixed_arguments=frozenset({"hosts", "ahostsv4", "ahostsv6"}),
        operand_validator=validate_host,
    ),
    "dig": CommandPolicy(
        max_timeout=15, max_stdout_bytes=256 * 1024,
        fixed_arguments=frozenset({"-r", "+time=3", "+tries=1", "+stats", "-q"}),
        operand_validator=validate_host, operand_after="-q",
    ),
}


@dataclass(frozen=True)
class _Violation:
    """A reason why a candidate file was not accepted. kind is "permission" or "untrusted"."""

    kind: str
    reason: str


def command_environment() -> dict[str, str]:
    """Return a new minimal environment. os.environ is never read."""
    return {"PATH": ":".join(TRUSTED_DIRECTORIES), "LC_ALL": "C", "LANG": "C"}


# --- Resolution of the executable ---


def _check_directory_chain(
    directory: str, trusted_uids: tuple[int, ...], stop_at: Optional[str],
) -> Optional[_Violation]:
    """Check a real directory and all its parents up to the root (or stop_at, excluded)."""
    current = directory
    while True:
        if stop_at is not None and current == stop_at:
            return None

        try:
            info = os.stat(current)
        except PermissionError as error:
            return _Violation("permission", f"cannot inspect {current}: {error.strerror}")
        except OSError as error:
            return _Violation("untrusted", f"cannot inspect {current}: {error.strerror}")

        if not stat.S_ISDIR(info.st_mode):
            return _Violation("untrusted", f"{current} is not a directory")
        if info.st_uid not in trusted_uids:
            return _Violation("untrusted", f"{current} is not owned by a trusted user")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return _Violation("untrusted", f"{current} is writable by group or others")

        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _symlink_directories(candidate: str) -> Optional[list[str]]:
    """Return the real directory of every link in a symlink chain, or None if the chain is too long."""
    directories = []
    current = candidate
    for _ in range(MAX_SYMLINKS + 1):
        parent = os.path.realpath(os.path.dirname(current))
        directories.append(parent)
        current = os.path.join(parent, os.path.basename(current))

        if not os.path.islink(current):
            return directories

        target = os.readlink(current)
        if not os.path.isabs(target):
            target = os.path.join(parent, target)
        current = target

    return None


def _check_candidate(
    candidate: str, trusted_uids: tuple[int, ...], stop_at: Optional[str],
) -> tuple[Optional[str], Optional[_Violation]]:
    """Return (real_path, None) for a trusted file, or (None, violation)."""
    try:
        directories = _symlink_directories(candidate)
    except OSError as error:
        return None, _Violation("untrusted", f"cannot follow {candidate}: {error.strerror}")
    if directories is None:
        return None, _Violation("untrusted", f"too many symbolic links at {candidate}")

    real = os.path.realpath(candidate)
    directories.append(os.path.dirname(real))

    try:
        info = os.stat(real)
    except PermissionError as error:
        return None, _Violation("permission", f"cannot inspect {real}: {error.strerror}")
    except OSError as error:
        return None, _Violation("untrusted", f"cannot inspect {real}: {error.strerror}")

    if not stat.S_ISREG(info.st_mode):
        return None, _Violation("untrusted", f"{real} is not a regular file")
    if info.st_uid not in trusted_uids:
        return None, _Violation("untrusted", f"{real} is not owned by a trusted user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None, _Violation("untrusted", f"{real} is writable by group or others")

    checked = []
    for directory in directories:
        if directory in checked:
            continue
        checked.append(directory)
        violation = _check_directory_chain(directory, trusted_uids, stop_at)
        if violation is not None:
            return None, violation

    # Effective ids, because the command is started with the effective user.
    if not os.access(real, os.X_OK, effective_ids=os.access in os.supports_effective_ids):
        return None, _Violation("permission", f"{real} is not executable by the current user")

    return real, None


def resolve_trusted_executable(
    name: str, directories: Optional[tuple[str, ...]] = None, *,
    trusted_uids: Optional[tuple[int, ...]] = None, stop_at: Optional[str] = None,
) -> str:
    """Return the real path of a trusted executable, or raise a CommandError.

    The first directory with a candidate that passes every check wins. A file must
    be a regular file, executable, owned by a trusted uid and not writable by group or
    others, and so must every directory above it, including those of symbolic links.
    directories and trusted_uids default to TRUSTED_DIRECTORIES and TRUSTED_OWNER_UIDS,
    read at call time. stop_at is for tests: the directory chain checks end there (excluded).
    The returned path is the real path, so a later change of a symbolic link has no effect.

    Raises CommandNotFoundError when no directory has the command,
    CommandPermissionError when a candidate cannot be executed and no other is
    trusted, and CommandUntrustedError when all candidates fail the trust checks.
    """
    if not name or "/" in name or name in (".", ".."):
        raise ValidationError("Command name must be a bare name")

    if directories is None:
        directories = TRUSTED_DIRECTORIES
    if trusted_uids is None:
        trusted_uids = TRUSTED_OWNER_UIDS

    real_stop_at = os.path.realpath(stop_at) if stop_at is not None else None
    violations = []
    seen = []
    for directory in directories:
        real_directory = os.path.realpath(directory)
        if real_directory in seen:
            continue
        seen.append(real_directory)

        candidate = os.path.join(directory, name)
        try:
            os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except PermissionError as error:
            violations.append(_Violation("permission", f"cannot inspect {candidate}: {error.strerror}"))
            continue
        except OSError as error:
            violations.append(_Violation("untrusted", f"cannot inspect {candidate}: {error.strerror}"))
            continue

        real, violation = _check_candidate(candidate, trusted_uids, real_stop_at)
        if violation is not None:
            violations.append(violation)
            continue

        return real

    if not violations:
        searched = ", ".join(directories)
        raise CommandNotFoundError(
            f"Required command not found: {name}",
            reason=f"not found in trusted directories ({searched})",
        )

    for violation in violations:
        if violation.kind == "permission":
            raise CommandPermissionError(
                f"Could not execute {name}: {violation.reason}", reason=violation.reason,
            )

    reason = violations[0].reason
    raise CommandUntrustedError(f"Refusing to run {name}: {reason}", reason=reason)


def _resolve_executable(name: str) -> str:
    """Single point where run_command resolves a command; tests replace it."""
    return resolve_trusted_executable(name, TRUSTED_DIRECTORIES, trusted_uids=TRUSTED_OWNER_UIDS)


# --- Bounded execution ---


def _decode(data) -> str:
    return bytes(data).decode("utf-8", errors="replace")


def _signal_group(group_id: int, signal_number: int, process: subprocess.Popen) -> None:
    try:
        os.killpg(group_id, signal_number)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Cleanup is best effort: at least stop the main process.
        try:
            process.kill()
        except OSError:
            pass


def _terminate_group(process: subprocess.Popen) -> None:
    """Stop the whole process group of a command and reap the main process.

    SIGTERM first, then SIGKILL after a short grace period (always, so that
    descendants that ignore SIGTERM also die, even if the wait is interrupted).
    Safe to call when the group is gone.
    """
    group_id = process.pid
    if group_id <= 1:
        # Never signal process group 0 (our own) or 1.
        process.kill()
        process.wait()
        return

    _signal_group(group_id, signal.SIGTERM, process)
    try:
        try:
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    finally:
        _signal_group(group_id, signal.SIGKILL, process)

    try:
        process.wait(timeout=REAP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        # An unkillable process (uninterruptible sleep): reap it later, leave no zombie.
        threading.Thread(target=process.wait, daemon=True).start()


def _read_streams(
    process: subprocess.Popen, selector: selectors.BaseSelector, deadline: float,
    buffers: dict[str, bytearray], limits: dict[str, int], max_total_bytes: int,
) -> tuple[str, str]:
    """Read stdout and stderr until both close, a limit is hit, or time runs out.

    Returns (status, reason). The status is READ_DONE, READ_ORPHANED (the main
    process ended but another process kept a pipe open), READ_TIMEOUT, or
    READ_LIMIT; only READ_LIMIT has a reason. Data is added to buffers as it is
    read, and never more than the limits allow is kept.
    """
    drain_deadline = None
    while selector.get_map():
        now = time.monotonic()
        if now >= deadline:
            return READ_TIMEOUT, ""

        wait = min(deadline - now, POLL_SLICE_SECONDS)
        if drain_deadline is not None:
            if now >= drain_deadline:
                return READ_ORPHANED, ""
            wait = min(wait, drain_deadline - now)

        for key, _ in selector.select(wait):
            name = key.data
            data = os.read(key.fd, READ_CHUNK_BYTES)
            if not data:
                selector.unregister(key.fileobj)
                continue

            buffer = buffers[name]
            total = len(buffers["stdout"]) + len(buffers["stderr"])
            keep = max(0, min(limits[name] - len(buffer), max_total_bytes - total))
            buffer += data[:keep]
            if len(data) > keep:
                if len(buffer) >= limits[name]:
                    return READ_LIMIT, f"{name} exceeded {limits[name]} bytes"
                return READ_LIMIT, f"total output exceeded {max_total_bytes} bytes"

        if drain_deadline is None and process.poll() is not None:
            drain_deadline = time.monotonic() + DRAIN_SECONDS

    return READ_DONE, ""


def _run_bounded(
    arguments: list[str], *, executable: str, timeout: float, env: dict[str, str],
    max_stdout_bytes: int, max_stderr_bytes: int, max_total_bytes: int,
) -> subprocess.CompletedProcess:
    """Run one process with hard limits and return a CompletedProcess with text output.

    The process runs without a shell, with no stdin, in "/" and in its own session,
    so that its whole process group can be stopped. The timeout covers everything
    after the start. Output is read in chunks and never kept beyond the limits.

    Raises subprocess.TimeoutExpired, CommandOutputLimitError, or the OSError
    from starting the process. The process group is always stopped on failure,
    including KeyboardInterrupt, and all pipes are closed.
    """
    selector = None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
    group_handled = False

    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        arguments, executable=executable, env=env, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, close_fds=True, pass_fds=(), cwd="/", start_new_session=True,
    )
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")

        status, reason = _read_streams(
            process, selector, deadline, buffers, limits, max_total_bytes,
        )

        returncode = None
        if status in (READ_DONE, READ_ORPHANED):
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                status = READ_TIMEOUT

        if status == READ_DONE:
            # The main process is reaped and both pipes are at EOF, so killpg is not
            # called: the process ID could already belong to another group. A
            # descendant that closed both pipes and stayed in the group survives.
            group_handled = True
            return subprocess.CompletedProcess(
                arguments, returncode, _decode(buffers["stdout"]), _decode(buffers["stderr"]),
            )

        _terminate_group(process)
        group_handled = True

        if status == READ_ORPHANED:
            return subprocess.CompletedProcess(
                arguments, returncode, _decode(buffers["stdout"]), _decode(buffers["stderr"]),
            )

        if status == READ_TIMEOUT:
            raise subprocess.TimeoutExpired(
                arguments, timeout, output=bytes(buffers["stdout"]), stderr=bytes(buffers["stderr"]),
            )

        raise CommandOutputLimitError(
            f"{arguments[0]} output limit exceeded: {reason}", arguments=arguments,
            reason=reason, stdout=_decode(buffers["stdout"]), stderr=_decode(buffers["stderr"]),
            timeout=timeout,
        )
    finally:
        # Also runs for KeyboardInterrupt, SystemExit and programming errors.
        try:
            if not group_handled:
                _terminate_group(process)
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()
            process.stderr.close()


# --- Validation ---


def _shown(value: str) -> str:
    shown = repr(value[:64])
    if len(value) > 64:
        shown += "..."
    return shown


def _validate_command_name(name: str) -> CommandPolicy:
    policy = COMMAND_POLICIES.get(name)
    if policy is None:
        raise ValidationError(f"Command is not allowed: {_shown(name)}")
    return policy


def _validate_command_grammar(arguments: list[str], policy: CommandPolicy) -> None:
    """Accept only fixed tokens, numbers after numeric flags, and one validated operand."""
    last = len(arguments) - 1
    for index in range(1, len(arguments)):
        token = arguments[index]
        previous = arguments[index - 1]

        if policy.operand_after is not None and previous == policy.operand_after:
            policy.operand_validator(token)
        elif previous in policy.numeric_flags and _NUMERIC_VALUE.fullmatch(token):
            continue
        elif token in policy.fixed_arguments:
            continue
        elif (
            index == last and policy.operand_after is None
            and policy.operand_validator is not None
        ):
            policy.operand_validator(token)
        else:
            raise ValidationError(f"Argument is not allowed for {arguments[0]}: {_shown(token)}")


def _validate_allowed_codes(allowed_codes: tuple[int, ...]) -> None:
    if (
        not isinstance(allowed_codes, tuple) or not allowed_codes
        or not all(isinstance(code, int) and not isinstance(code, bool) for code in allowed_codes)
    ):
        raise ValidationError("allowed_codes must be a non-empty tuple of integers")


def _printable(text: str) -> str:
    """Replace non-printable characters (controls, bidi marks, ...) with escapes."""
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


def _error_detail(stderr: str, return_code: int) -> str:
    """Return the first non-empty stderr line, escaped and shortened, or the exit code."""
    for line in stderr.splitlines():
        detail = _printable(line.strip())
        if detail:
            if len(detail) > MAX_ERROR_DETAIL_CHARS:
                return detail[:MAX_ERROR_DETAIL_CHARS] + TRUNCATED_MARKER
            return detail
    return f"exit code {return_code}"


def _complete(error: CommandError, arguments: list[str], timeout: float) -> None:
    """Fill in the caller's details on an error raised below run_command."""
    error.arguments = list(arguments)
    error.timeout = timeout


def _translate_start_error(error: Exception, arguments: list[str], timeout: float) -> CommandError:
    """Map an exception from starting the process to a CommandError."""
    if isinstance(error, FileNotFoundError):
        return CommandNotFoundError(
            f"Required command not found: {arguments[0]}",
            arguments=arguments, reason=str(error), timeout=timeout,
        )

    if isinstance(error, PermissionError):
        error_class = CommandPermissionError
    else:
        error_class = CommandStartError
    return error_class(
        f"Could not execute {arguments[0]}: {error}",
        arguments=arguments, reason=str(error), timeout=timeout,
    )


def run_command(
    arguments: list[str], allowed_codes: tuple[int, ...] = (0,), timeout: float = 10,
) -> CommandResult:
    validate_command_arguments(arguments)
    arguments = list(arguments)
    policy = _validate_command_name(arguments[0])
    _validate_command_grammar(arguments, policy)
    validate_timeout(timeout)
    if timeout > policy.max_timeout:
        raise ValidationError(
            f"timeout for {arguments[0]} must be at most {policy.max_timeout} seconds"
        )
    _validate_allowed_codes(allowed_codes)

    try:
        executable = _resolve_executable(arguments[0])
    except CommandError as error:
        _complete(error, arguments, timeout)
        raise

    started = time.monotonic()
    try:
        # Output is decoded as UTF-8 regardless of the caller's locale. Invalid
        # bytes become U+FFFD instead of raising UnicodeDecodeError, which is not
        # a DiagnosticError. Parsers reject the replacement character in
        # validated fields.
        result = _run_bounded(
            arguments, executable=executable, timeout=timeout, env=command_environment(),
            max_stdout_bytes=policy.max_stdout_bytes, max_stderr_bytes=MAX_STDERR_BYTES,
            max_total_bytes=MAX_TOTAL_OUTPUT_BYTES,
        )
    except CommandError as error:
        _complete(error, arguments, timeout)
        raise
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise CommandTimeoutError(
            f"{arguments[0]} timed out after {timeout} seconds.",
            arguments=arguments, reason=str(error), stdout=stdout, stderr=stderr,
            timeout=timeout,
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise _translate_start_error(error, arguments, timeout) from error

    duration = time.monotonic() - started
    if result.returncode not in allowed_codes:
        detail = _error_detail(result.stderr, result.returncode)
        raise CommandExecutionError(
            f"{arguments[0]} failed: {detail}", arguments=arguments,
            reason=detail, return_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr, timeout=timeout,
        )

    return CommandResult(
        arguments=arguments, stdout=result.stdout, stderr=result.stderr,
        return_code=result.returncode, duration_seconds=duration,
    )
