import os
import subprocess
import time

from network_diagnostic.core.exceptions import CommandExecutionError, CommandNotFoundError, CommandTimeoutError
from network_diagnostic.core.models import CommandResult
from network_diagnostic.core.validation import validate_command_arguments, validate_timeout


def command_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    return environment


def run_command(
    arguments: list[str], allowed_codes: tuple[int, ...] = (0,), timeout: float = 10,
) -> CommandResult:
    validate_command_arguments(arguments)
    validate_timeout(timeout)

    arguments = list(arguments)
    started = time.monotonic()
    try:
        # Output is decoded as UTF-8 regardless of the caller's locale. Invalid
        # bytes become U+FFFD instead of raising UnicodeDecodeError, which is not
        # a DiagnosticError. Parsers reject the replacement character in
        # validated fields.
        result = subprocess.run(
            arguments, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=command_environment(), shell=False, check=False,
        )
    except FileNotFoundError as error:
        raise CommandNotFoundError(
            f"Required command not found: {arguments[0]}",
            arguments=arguments, reason=str(error), timeout=timeout,
        ) from error
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
    except OSError as error:
        raise CommandExecutionError(
            f"Could not execute {arguments[0]}: {error}",
            arguments=arguments, reason=str(error), timeout=timeout,
        ) from error

    duration = time.monotonic() - started
    if result.returncode not in allowed_codes:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise CommandExecutionError(
            f"{arguments[0]} failed: {detail}", arguments=arguments,
            reason=detail, return_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr, timeout=timeout,
        )

    return CommandResult(
        arguments=arguments, stdout=result.stdout, stderr=result.stderr,
        return_code=result.returncode, duration_seconds=duration,
    )
