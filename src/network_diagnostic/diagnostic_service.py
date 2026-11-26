"""UI-independent application interface to the diagnostic engine.

The CLI and a future GUI call these functions instead of the engine modules.
The functions are synchronous, keep no state between calls, never print or
prompt, and return the dataclasses from network_diagnostic.core.models.

Error contract:

- Invalid caller input raises ValidationError before any command runs. Ping
  keeps its own contract and returns a failed PingResult instead.
- A function built on one diagnostic operation lets CommandError and
  ParsingError propagate. Measurements, including negative ones, are results.
- A function built on independent operations returns the parts that succeeded
  and records the error of each failed part in its result.
- Programming errors are never caught.
"""

from typing import Sequence

from network_diagnostic.core import analysis, dns, network, parsers, ping
from network_diagnostic.core.exceptions import CommandError, CommandTimeoutError, ParsingError, ValidationError
from network_diagnostic.core.models import (
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    ListeningPortReport, NetworkInterface, PingResult, RoutingSnapshot,
)
from network_diagnostic.core.validation import validate_host


DEFAULT_DNS_HOSTNAME = "google.com"
ADDRESS_LOOKUP_HOSTNAMES = ("google.com", "wikipedia.com", "example.com", "yle.fi")

# Errors of an independent part that are recorded instead of raised.
_PART_ERRORS = (CommandError, ParsingError)


def get_connection_status() -> bool:
    """Return True when NetworkManager reports a connected non-loopback device.

    Raises CommandError or ParsingError when the status cannot be read.
    """
    return network.get_network_plugin()


def run_ping_diagnostic(target: str) -> PingResult:
    """Ping a predefined target: "localhost", "gateway", "internet" or "google.com".

    Never raises a DiagnosticError: unknown targets, invalid input and technical
    failures are returned as a FAILED or TIMEOUT PingResult with an error.
    """
    return ping.run_ping_diagnostic(target)


def run_dns_query(hostname: str = DEFAULT_DNS_HOSTNAME) -> DnsQueryResult:
    """Resolve a hostname with the system resolver.

    A name that is not found is a completed result with resolved=False.
    Raises ValidationError for an invalid hostname and CommandError when getent fails.
    """
    return dns.dns_query(hostname)


def run_dns_latency_test(hostname: str = DEFAULT_DNS_HOSTNAME) -> DnsLatencyResult:
    """Measure the DNS response time and classify it when it was measured.

    latency_analysis stays None when no response time is available. Raises
    ValidationError for an invalid hostname and CommandError when dig fails.
    """
    result = dns.dns_latency(hostname)

    if result.dns_latency_ms is not None:
        result.latency_analysis = analysis.dns_latency_analyzation(result)

    return result


def lookup_ip_addresses(hostnames: Sequence[str] = ADDRESS_LOOKUP_HOSTNAMES) -> list[DnsAddressResult]:
    """Look up the IPv4 and IPv6 addresses of each hostname, in the given order.

    All hostnames are validated before any lookup, and an invalid one raises
    ValidationError. A failed lookup does not stop the others: its result has
    execution status FAILED or TIMEOUT, an error and empty address lists.
    Accepts a list or a tuple of hostnames.
    """
    if not isinstance(hostnames, (list, tuple)):
        raise ValidationError("hostnames must be a list or tuple of strings")

    for hostname in hostnames:
        validate_host(hostname)

    results = []
    for hostname in hostnames:
        try:
            result = dns.ipv_check(hostname)
        except CommandTimeoutError as error:
            result = DnsAddressResult(
                hostname=hostname, execution_status=ExecutionStatus.TIMEOUT, error=str(error),
            )
        except _PART_ERRORS as error:
            result = DnsAddressResult(
                hostname=hostname, execution_status=ExecutionStatus.FAILED, error=str(error),
            )
        results.append(result)

    return results


def get_listening_ports() -> ListeningPortReport:
    """Return listening TCP and unconnected UDP sockets, their counts and service names.

    The result is all-or-nothing: an unrecognized `ss` line raises ParsingError
    so that a partial list is never presented as complete. Raises CommandError
    when ss fails.
    """
    rows = parsers.parse_ss_output(network.get_listening_sockets())
    sockets = analysis.get_open_ports(rows)

    return ListeningPortReport(
        sockets=sockets,
        counts=analysis.count_open_ports(sockets),
        services=analysis.analyze_open_ports(sockets),
    )


def get_routing_snapshot() -> RoutingSnapshot:
    """Return the hostname, the default-route interface, IPv4 address and gateway.

    The routing table is read once. The hostname does not depend on it, so each
    is collected even if the other fails. The interface, address and gateway are
    read from the routing table only when it is available. Failed parts are
    recorded in `errors` instead of being raised.
    """
    snapshot = RoutingSnapshot()

    try:
        snapshot.routes = network.get_ip_route()
    except _PART_ERRORS as error:
        snapshot.errors["routes"] = str(error)

    try:
        snapshot.hostname = network.get_hostname()
    except _PART_ERRORS as error:
        snapshot.errors["hostname"] = str(error)

    if snapshot.routes is None:
        return snapshot

    route_parts = (
        ("interface", network.get_nic),
        ("ip_address", network.get_ip),
        ("gateway", network.get_gateway),
    )
    for name, read_value in route_parts:
        try:
            setattr(snapshot, name, read_value(snapshot.routes))
        except _PART_ERRORS as error:
            snapshot.errors[name] = str(error)

    return snapshot


def get_network_interfaces() -> list[NetworkInterface]:
    """Return NetworkManager device states and connection names.

    Raises CommandError when nmcli fails.
    """
    return parsers.parse_nmcli_output(network.nmcli_status())
