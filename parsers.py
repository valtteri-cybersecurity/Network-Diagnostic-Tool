"""Pure parsers for diagnostic command output.

Parsers never run commands, print, prompt or change external state. They
validate parsed values before building result models and raise ParsingError
when output is malformed, contradictory or implausible. A missing optional
measurement is returned as None, never as zero.
"""

import ipaddress
import re
from typing import Callable, Optional

from exceptions import ParsingError, ValidationError
from models import DnsLatencyResult, ExecutionStatus, ListeningSocket, NetworkInterface, PingResult
from validation import validate_interface_name, validate_ipv4_address


MAX_PORT = 65535
# Ping prints loss rounded (%g in iputils, %.1f on macOS) or truncated to an
# integer (BusyBox). Any difference below one percentage point is rounding.
LOSS_TOLERANCE_PERCENT = 1.0

_COUNT = re.compile(r"[0-9]+")
_NON_NEGATIVE_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_PORT = re.compile(r"[0-9]{1,5}")
_DNS_STATUS = re.compile(r"[A-Z][A-Z0-9]*")
_DIG_STATUS = re.compile(r"status:\s*([^,\s]*)")
_DIG_ANSWER = re.compile(r"ANSWER:\s*([^,\s]*)")
_DIG_QUERY_TIME = re.compile(r"Query time:([^\n]*)")
_DIG_QUERY_TIME_VALUE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*msec\s*")


def _parse_count(text: str) -> int:
    if not _COUNT.fullmatch(text):
        raise ParsingError("Unable to parse packet statistics")
    return int(text)


def _parse_non_negative_number(text: str, message: str) -> float:
    # The pattern rejects signs, exponents, NaN and infinity before float().
    if not _NON_NEGATIVE_NUMBER.fullmatch(text):
        raise ParsingError(message)
    return float(text)


def _validated(validator: Callable[[str], None], value: str, description: str) -> str:
    try:
        validator(value)
    except ValidationError as error:
        raise ParsingError(f"Invalid {description}: {error}") from error
    return value


def parse_port(text: str) -> int:
    """Return a decimal port number in the range 0-65535."""
    if not isinstance(text, str) or not _PORT.fullmatch(text) or int(text) > MAX_PORT:
        raise ParsingError("Invalid port number")
    return int(text)


def parse_ping_statistics(output: str, target: str) -> PingResult:
    """Return validated packet and RTT statistics from ping output."""
    lines = output.splitlines()
    # Hostnames cannot contain spaces, so a target such as "transmitted.example"
    # in the PING header is not mistaken for the summary line.
    summaries = [line for line in lines if "packets transmitted" in line]
    rtt_lines = [line for line in lines if line.startswith("rtt ") or line.startswith("round-trip ")]

    if not summaries:
        raise ParsingError("No packet statistics found")
    if len(summaries) > 1:
        raise ParsingError("Multiple packet statistics found")
    if len(rtt_lines) > 1:
        raise ParsingError("Multiple latency statistics found")

    sent = None
    received = None
    loss = None
    # Other fields, such as "+1 duplicates", "+3 errors" and "time", are ignored.
    # Duplicate replies are reported separately and are not part of "received".
    for action in summaries[0].split(","):
        words = action.split()
        if "transmitted" in action:
            sent = _parse_count(words[0])
        elif "received" in action:
            received = _parse_count(words[0])
        elif "packet loss" in action:
            if not words[0].endswith("%"):
                raise ParsingError("Unable to parse packet statistics")
            loss = _parse_non_negative_number(words[0][:-1], "Unable to parse packet statistics")

    if sent is None or received is None or loss is None:
        raise ParsingError("Incomplete packet statistics")
    if sent == 0:
        raise ParsingError("Invalid packet statistics: no packets transmitted")
    if received > sent:
        raise ParsingError("Invalid packet statistics: more packets received than transmitted")
    if loss > 100:
        raise ParsingError("Invalid packet statistics: packet loss outside 0-100%")
    expected_loss = (sent - received) * 100 / sent
    if abs(loss - expected_loss) >= LOSS_TOLERANCE_PERCENT:
        raise ParsingError("Invalid packet statistics: packet loss does not match packet counts")

    report = PingResult(
        target=target, execution_status=ExecutionStatus.COMPLETED,
        packets_sent=sent, packets_received=received, packet_loss_percent=loss,
        reachable=received > 0, raw_output=output,
    )

    if not rtt_lines:
        # Complete loss without an RTT summary is a valid measurement.
        if received == 0:
            return report
        raise ParsingError("No latency data available")

    rtt_line = rtt_lines[0]
    if "=" not in rtt_line:
        raise ParsingError("Unable to parse latency statistics")
    values = rtt_line.split("=", 1)[1].replace(" ms", "").strip().split("/")
    if len(values) != 4:
        raise ParsingError("Unable to parse latency statistics")
    minimum, average, maximum, deviation = [
        _parse_non_negative_number(value, "Unable to parse latency statistics") for value in values
    ]

    if received == 0:
        raise ParsingError("Invalid latency statistics: latency reported without received packets")
    if not minimum <= average <= maximum:
        raise ParsingError("Invalid latency statistics: expected min <= avg <= max")

    report.min_rtt_ms = minimum
    report.avg_rtt_ms = average
    report.max_rtt_ms = maximum
    report.mdev_ms = deviation
    return report


def _check_getent_address(text: str) -> None:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        raise ParsingError("Invalid address in getent output") from None


def parse_getent_hosts(output: str) -> Optional[str]:
    """Return the first address from `getent hosts` output, or None if there is none.

    Every non-empty line must start with a valid IPv4 or IPv6 address.
    """
    first_address = None

    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue

        _check_getent_address(parts[0])
        if first_address is None:
            first_address = parts[0]

    return first_address


def parse_getent_addresses(output: str, version: int) -> list[str]:
    """Return unique addresses of one IP version from `getent ahostsv4/ahostsv6` output.

    Invalid addresses and addresses of the other version are skipped.
    """
    addresses = []

    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue

        try:
            address = ipaddress.ip_address(parts[0])
        except ValueError:
            continue

        if address.version != version:
            continue

        value = str(address)
        if value not in addresses:
            addresses.append(value)

    return addresses


def _single_value(pattern: re.Pattern, output: str, description: str) -> Optional[str]:
    values = pattern.findall(output)
    if not values:
        return None
    for value in values:
        if value != values[0]:
            raise ParsingError(f"Conflicting DNS {description} values")
    return values[0]


def parse_dig_output(output: str, hostname: str) -> DnsLatencyResult:
    """Return the DNS response code, answer count and query time from `dig +stats` output.

    The response code and the query time are kept separate: a fast response
    does not mean that the name was resolved.
    """
    status = _single_value(_DIG_STATUS, output, "response status")
    if status is None:
        raise ParsingError("Unable to parse DNS response status")
    if not _DNS_STATUS.fullmatch(status):
        raise ParsingError("Invalid DNS response status")

    answer_count = None
    answer_text = _single_value(_DIG_ANSWER, output, "answer count")
    if answer_text is not None:
        if not _COUNT.fullmatch(answer_text):
            raise ParsingError("Invalid DNS answer count")
        answer_count = int(answer_text)

    latency = None
    time_text = _single_value(_DIG_QUERY_TIME, output, "query time")
    if time_text is not None:
        match = _DIG_QUERY_TIME_VALUE.fullmatch(time_text)
        if match is None:
            raise ParsingError("Invalid DNS query time")
        latency = float(match.group(1))

    resolved = None
    if status != "NOERROR":
        resolved = False
    elif answer_count is not None:
        resolved = answer_count > 0

    return DnsLatencyResult(
        hostname=hostname, execution_status=ExecutionStatus.COMPLETED,
        dns_status=status, resolved=resolved, dns_latency_ms=latency,
        answer_count=answer_count,
    )


def split_nmcli_fields(line: str) -> list[str]:
    """Split one `nmcli -t -e yes` line into fields, honoring backslash escapes."""
    fields = []
    characters = []
    escaped = False

    for character in line:
        if escaped:
            characters.append(character)
            escaped = False

        elif character == "\\":
            escaped = True

        elif character == ":":
            fields.append("".join(characters))
            characters = []

        else:
            characters.append(character)

    if escaped:
        characters.append("\\")

    fields.append("".join(characters))

    return fields


def parse_nmcli_output(lines: list[str]) -> list[NetworkInterface]:
    """Return interfaces from DEVICE,TYPE,STATE,CONNECTION lines.

    Lines with a wrong field count or without a device or state are skipped.
    """
    parsed = []

    for line in lines:
        parts = split_nmcli_fields(line)

        if len(parts) != 4:
            continue

        device, interface_type, state, connection = parts

        if not device or not state:
            continue

        parsed.append(NetworkInterface(
            device=device, interface_type=interface_type,
            state=state, connection=connection,
        ))

    return parsed


def parse_nmcli_states(output: str) -> list[tuple[str, str]]:
    """Return (device, state) pairs from DEVICE,STATE lines, skipping malformed lines."""
    states = []

    for line in output.splitlines():
        parts = split_nmcli_fields(line)

        if len(parts) != 2 or not parts[0] or not parts[1]:
            continue

        states.append((parts[0], parts[1]))

    return states


def _check_socket_address(address: str) -> None:
    # ss prints `*`, IPv4 or bracketed IPv6 addresses. An interface scope may be
    # inside the brackets (`[fe80::1%eth0]`) or after the address (`[fe80::1]%eth0`,
    # `0.0.0.0%eth0`, `*%lo`).
    host = address
    scope = None

    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            raise ParsingError("Invalid socket address")
        suffix = host[closing + 1:]
        host = host[1:closing]
        if suffix:
            if not suffix.startswith("%"):
                raise ParsingError("Invalid socket address")
            scope = suffix[1:]

    if "%" in host:
        if scope is not None:
            raise ParsingError("Invalid socket address")
        host, scope = host.split("%", 1)

    if scope is not None:
        _validated(validate_interface_name, scope, "socket address scope")

    if host == "*":
        return

    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ParsingError("Invalid socket address") from None
    else:
        _validated(validate_ipv4_address, host, "socket address")


def _parse_socket_endpoint(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ParsingError("Missing socket port")

    address, port = text.rsplit(":", 1)
    if port != "*":
        parse_port(port)
    _check_socket_address(address)

    return address, port


def parse_ss_output(lines: list[str]) -> list[ListeningSocket]:
    """Return sockets from `ss -H -tuln` lines.

    Ports must be `*` or 0-65535, and addresses `*`, IPv4 or IPv6 with an
    optional `%interface` scope. Lines that do not meet this are skipped.
    """
    sockets = []

    for line in lines:
        parts = line.split()

        if len(parts) < 6 or parts[0] == "Netid":
            continue

        try:
            local_address, local_port = _parse_socket_endpoint(parts[4])
            peer_address, peer_port = _parse_socket_endpoint(parts[5])
        except ParsingError:
            continue

        sockets.append(ListeningSocket(
            protocol=parts[0], state=parts[1],
            local_address=local_address, local_port=local_port,
            peer_address=peer_address, peer_port=peer_port,
        ))

    return sockets


def _default_route_value(route_lines: list[str], keyword: str) -> Optional[str]:
    # Each value is taken from the first default route that contains the keyword.
    # Choosing a single default route belongs to a later phase (KNOWN_ISSUES 1).
    for line in route_lines:
        parts = line.split()

        if not parts or parts[0] != "default":
            continue

        if keyword in parts:
            index = parts.index(keyword)

            if index + 1 < len(parts):
                return parts[index + 1]

    return None


def parse_default_gateway(route_lines: list[str]) -> Optional[str]:
    """Return the validated IPv4 gateway (`via`) of a default route, or None."""
    gateway = _default_route_value(route_lines, "via")
    if gateway is None:
        return None
    return _validated(validate_ipv4_address, gateway, "default gateway")


def parse_default_interface(route_lines: list[str]) -> Optional[str]:
    """Return the validated interface name (`dev`) of a default route, or None."""
    interface = _default_route_value(route_lines, "dev")
    if interface is None:
        return None
    return _validated(validate_interface_name, interface, "default route interface")


def parse_default_source(route_lines: list[str]) -> Optional[str]:
    """Return the validated IPv4 source address (`src`) of a default route, or None."""
    source = _default_route_value(route_lines, "src")
    if source is None:
        return None
    return _validated(validate_ipv4_address, source, "default route source address")


def parse_interface_ipv4_address(output: str) -> Optional[str]:
    """Return the first validated IPv4 address from `ip -4 -o addr show` output, or None."""
    for line in output.splitlines():
        parts = line.split()

        if "inet" in parts:
            index = parts.index("inet")

            if index + 1 < len(parts):
                address = parts[index + 1].split("/")[0]
                return _validated(validate_ipv4_address, address, "interface address")

    return None
