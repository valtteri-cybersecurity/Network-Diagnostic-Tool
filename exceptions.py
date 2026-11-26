from typing import Optional


class DiagnosticError(Exception):
    pass


class ValidationError(DiagnosticError):
    """Raised when an input is rejected before any command is executed."""


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
