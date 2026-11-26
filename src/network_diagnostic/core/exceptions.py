from typing import Optional


class DiagnosticError(Exception):
    pass


class ValidationError(DiagnosticError):
    """Raised when an input is rejected before any command is executed."""


class ParsingError(DiagnosticError):
    """Raised when command output is malformed, contradictory or implausible."""


class CommandError(DiagnosticError):
    def __init__(
        self, message: str, *, arguments: Optional[list[str]] = None,
        reason: Optional[str] = None, return_code: Optional[int] = None,
        stdout: str = "", stderr: str = "", timeout: Optional[float] = None,
    ):
        super().__init__(message)
        self.arguments = list(arguments) if arguments is not None else []
        self.reason = reason if reason is not None else message
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout


class CommandNotFoundError(CommandError):
    pass


class CommandTimeoutError(CommandError):
    pass


class CommandExecutionError(CommandError):
    pass


class CommandUntrustedError(CommandNotFoundError):
    """The command exists, but no candidate passed the trusted-location checks.

    Subclass of CommandNotFoundError so that it is reported as unavailable.
    """


class CommandPermissionError(CommandExecutionError):
    """The command could not be executed because of missing permission."""


class CommandStartError(CommandExecutionError):
    """The command could not be started for a reason other than a missing file or permission."""


class CommandOutputLimitError(CommandExecutionError):
    """The command produced more output than the runner allows; its process group was stopped."""
