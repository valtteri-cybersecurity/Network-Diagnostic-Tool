"""Input validators used before diagnostic commands are executed.

Each validator returns None for an accepted value and raises ValidationError
for a rejected value. Values are never converted or corrected silently.
"""

import ipaddress
import math
import re

from exceptions import ValidationError


MAX_HOSTNAME_LENGTH = 253

_HOSTNAME_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_DECIMAL_OCTET = re.compile(r"0|[1-9][0-9]{0,2}")
_DIGITS_AND_DOTS = re.compile(r"[0-9.]+")
_INTERFACE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,15}")
_IPV6_CHARACTERS = re.compile(r"[0-9A-Fa-f:.]+")


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

    # Values made only of digits and dots must be valid IPv4 literals instead.
    if _DIGITS_AND_DOTS.fullmatch(name):
        return False

    for label in name.split("."):
        if not _HOSTNAME_LABEL.fullmatch(label):
            return False

    return True


def validate_host(value: str) -> None:
    """Accept an IPv4 literal, an IPv6 literal without scope, or an ASCII hostname."""
    if not isinstance(value, str):
        raise ValidationError("host must be a string")

    if value[:1] in ("-", "+", "@"):
        raise ValidationError(f"Invalid host: {value!r} looks like a command option")

    if ":" in value:
        if _is_ipv6_address(value):
            return
    elif _is_ipv4_address(value) or _is_hostname(value):
        return

    raise ValidationError(f"Invalid host: {value!r}")


def validate_ipv4_address(value: str) -> None:
    """Accept only a dotted-decimal IPv4 address with four parts."""
    if not isinstance(value, str):
        raise ValidationError("IPv4 address must be a string")

    if not _is_ipv4_address(value):
        raise ValidationError(f"Invalid IPv4 address: {value!r}")


def validate_interface_name(value: str) -> None:
    """Accept a conservative ASCII network interface name of 1-15 characters."""
    if not isinstance(value, str):
        raise ValidationError("interface name must be a string")

    if (
        not _INTERFACE_NAME.fullmatch(value)
        or value.startswith("-")
        or value in (".", "..")
    ):
        raise ValidationError(f"Invalid interface name: {value!r}")


def validate_command_arguments(arguments: list[str]) -> None:
    """Accept a non-empty list of strings whose first item names a program."""
    if (
        not isinstance(arguments, list) or not arguments
        or not all(isinstance(argument, str) for argument in arguments)
        or not arguments[0]
    ):
        raise ValidationError("arguments must be a non-empty list of strings with a command name")

    for argument in arguments:
        if "\0" in argument:
            raise ValidationError("arguments must not contain NUL characters")


def validate_timeout(timeout: float) -> None:
    """Accept a positive, finite int or float. Booleans are rejected."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValidationError("timeout must be a finite positive number")

    try:
        value = float(timeout)
    except OverflowError:
        raise ValidationError("timeout must be a finite positive number") from None

    if not math.isfinite(value) or value <= 0:
        raise ValidationError("timeout must be a finite positive number")
