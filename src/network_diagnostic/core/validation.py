"""Input validators used before diagnostic commands are executed.

Each validator returns None for an accepted value and raises ValidationError
for a rejected value. Values are never converted or corrected silently.
"""

import ipaddress
import re

from network_diagnostic.core.exceptions import ValidationError


MAX_HOSTNAME_LENGTH = 253
MAX_TIMEOUT_SECONDS = 60
MAX_SHOWN_LENGTH = 64
MAX_ARGUMENTS = 32
MAX_ARGUMENT_LENGTH = 512

_HOSTNAME_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_DECIMAL_OCTET = re.compile(r"0|[1-9][0-9]{0,2}")
# A decimal, octal or hexadecimal part accepted by inet_aton(), such as 127 or 0x7f.
_NUMERIC_LABEL = re.compile(r"0[xX][0-9A-Fa-f]*|[0-9]+")
_INTERFACE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,15}")
_IPV6_CHARACTERS = re.compile(r"[0-9A-Fa-f:.]+")
# Control characters other than NUL, which has its own message.
_CONTROL_CHARACTERS = re.compile(r"[\x01-\x1f\x7f]")


def _shown(value: str) -> str:
    """Return at most MAX_SHOWN_LENGTH characters of value with escaped control characters."""
    shown = repr(value[:MAX_SHOWN_LENGTH])
    if len(value) > MAX_SHOWN_LENGTH:
        shown += "..."
    return shown


def _is_ipv4_address(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False

    for part in parts:
        if not _DECIMAL_OCTET.fullmatch(part) or int(part) > 255:
            return False

    return True


def _is_ipv6_address(value: str) -> bool:
    # Rejects scope IDs, prefixes and brackets before ipaddress parses the value.
    if not _IPV6_CHARACTERS.fullmatch(value):
        return False

    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False

    return True


def _is_hostname(value: str) -> bool:
    name = value
    if name.endswith("."):
        name = name[:-1]

    if not name or len(name) > MAX_HOSTNAME_LENGTH:
        return False

    labels = name.split(".")

    # Names made only of numeric parts, such as 999.1.2.3 or 0x7f.1, would be read
    # as alternative IPv4 forms by the resolver. They must be valid IPv4 literals.
    if all(_NUMERIC_LABEL.fullmatch(label) for label in labels):
        return False

    for label in labels:
        if not _HOSTNAME_LABEL.fullmatch(label):
            return False

    return True


def validate_host(value: str) -> None:
    """Accept an IPv4 literal, an IPv6 literal without scope, or an ASCII hostname."""
    if not isinstance(value, str):
        raise ValidationError("host must be a string")

    if value[:1] in ("-", "+", "@"):
        raise ValidationError(f"Invalid host: {_shown(value)} looks like a command option")

    if ":" in value:
        if _is_ipv6_address(value):
            return
    elif _is_ipv4_address(value) or _is_hostname(value):
        return

    raise ValidationError(f"Invalid host: {_shown(value)}")


def validate_ipv4_address(value: str) -> None:
    """Accept only a dotted-decimal IPv4 address with four parts."""
    if not isinstance(value, str):
        raise ValidationError("IPv4 address must be a string")

    if not _is_ipv4_address(value):
        raise ValidationError(f"Invalid IPv4 address: {_shown(value)}")


def validate_interface_name(value: str) -> None:
    """Accept a conservative ASCII network interface name of 1-15 characters."""
    if not isinstance(value, str):
        raise ValidationError("interface name must be a string")

    if (
        not _INTERFACE_NAME.fullmatch(value)
        or value.startswith("-")
        or value in (".", "..")
    ):
        raise ValidationError(f"Invalid interface name: {_shown(value)}")


def validate_command_arguments(arguments: list[str]) -> None:
    """Accept a short non-empty list of strings whose first item names a program.

    Arguments must not contain control characters (including NUL) and are limited
    to MAX_ARGUMENTS items of at most MAX_ARGUMENT_LENGTH characters each.
    """
    if (
        not isinstance(arguments, list) or not arguments
        or not all(isinstance(argument, str) for argument in arguments)
        or not arguments[0]
    ):
        raise ValidationError("arguments must be a non-empty list of strings with a command name")

    if len(arguments) > MAX_ARGUMENTS:
        raise ValidationError(f"arguments must contain at most {MAX_ARGUMENTS} items")

    for argument in arguments:
        if len(argument) > MAX_ARGUMENT_LENGTH:
            raise ValidationError(f"arguments must be at most {MAX_ARGUMENT_LENGTH} characters long")

        if "\0" in argument:
            raise ValidationError("arguments must not contain NUL characters")

        if _CONTROL_CHARACTERS.search(argument):
            raise ValidationError("arguments must not contain control characters")


def validate_timeout(timeout: float) -> None:
    """Accept an int or float greater than 0 and at most MAX_TIMEOUT_SECONDS.

    Booleans are rejected. The comparison also rejects NaN, infinities and very
    large integers without converting them to float.
    """
    message = f"timeout must be a positive number of at most {MAX_TIMEOUT_SECONDS} seconds"
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValidationError(message)

    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ValidationError(message)
