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

collect_diagnostic_snapshot() combines the operations below into one
DiagnosticSnapshot. It validates all input before any command runs, runs the
operations one after another and records each failed, timed out, unavailable,
unsupported or unrequested operation in its own CollectionResult. Only
CommandTimeoutError, CommandNotFoundError, CommandError and ParsingError are
mapped to results; programming errors propagate. On other platforms than Linux
no command runs.

Network traffic of the default request: the pings to "internet" and
"google.com" send ICMP packets to external hosts, and the DNS query, DNS
latency test and address lookups send DNS requests for google.com,
wikipedia.com, example.com and yle.fi through the system resolver. Purely local
collection is possible with ping_targets=(), address_hostnames=() and
include_dns_query=False and include_dns_latency=False; the include_* flags and
empty target tuples disable the matching operations. The "localhost" and
"gateway" pings stay on the local host and local network.
"""

from datetime import datetime, timezone
import re
import sys
import time
from typing import Sequence
import uuid

from network_diagnostic.core import analysis, dns, network, parsers, ping
from network_diagnostic.core.exceptions import (
    CommandError, CommandNotFoundError, CommandTimeoutError, ParsingError, ValidationError,
)
from network_diagnostic.core.models import (
    COLLECTION_ISSUE_MESSAGES, COLLECTION_SOURCES, CollectionIssue, CollectionResult, CollectionStatus,
    DiagnosticSnapshot, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    ListeningPortReport, ListeningSocket, NetworkInterface, PingResult, RoutingSnapshot,
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


DEFAULT_PING_TARGETS = ("localhost", "gateway", "internet", "google.com")
_MAX_TARGETS = 4
_ROUTING_FIELDS = ("hostname", "interface", "ip_address", "gateway", "routes")
_ROUTING_DEPENDENTS = ("interface", "ip_address", "gateway")
_HOST_STRUCTURE = re.compile(r"[A-Za-z0-9.:-]{1,254}")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _new_snapshot_id() -> str:
    # uuid4 is random; uuid1 would leak the MAC address.
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


def _validate_host_argument(hostname) -> None:
    if type(hostname) is not str:
        raise ValidationError("hostname must be a string")
    validate_host(hostname)
    if _HOST_STRUCTURE.fullmatch(hostname) is None or hostname.startswith("-"):
        raise ValidationError("hostname has an unsupported structure")


def _validate_snapshot_request(ping_targets, address_hostnames, dns_hostname, flags):
    """Validate every argument and return the targets as new tuples."""
    if type(ping_targets) not in (list, tuple) or type(address_hostnames) not in (list, tuple):
        raise ValidationError("targets must be a list or tuple of strings")
    ping_targets = tuple(ping_targets)
    address_hostnames = tuple(address_hostnames)

    if len(ping_targets) > _MAX_TARGETS or len(address_hostnames) > _MAX_TARGETS:
        raise ValidationError("at most four targets are allowed per group")
    for target in ping_targets:
        if type(target) is not str or target not in DEFAULT_PING_TARGETS:
            raise ValidationError("unknown ping target")
    for hostname in address_hostnames:
        _validate_host_argument(hostname)
    if len(set(ping_targets)) != len(ping_targets) or len(set(address_hostnames)) != len(address_hostnames):
        raise ValidationError("targets must not contain duplicates")
    _validate_host_argument(dns_hostname)
    for flag in flags:
        if type(flag) is not bool:
            raise ValidationError("include flags must be booleans")

    return ping_targets, address_hostnames


def _issue(code: str, field=None) -> CollectionIssue:
    return CollectionIssue(code=code, message=COLLECTION_ISSUE_MESSAGES[code], field=field)


def _invalid_outcome():
    return CollectionStatus.FAILED, None, (_issue("invalid_result"),)


def _execution_outcome(legacy, value_type, detach):
    """Map a legacy result with an execution status; None means an invalid object."""
    if type(legacy) is not value_type:
        return None
    if legacy.execution_status is ExecutionStatus.COMPLETED:
        return CollectionStatus.COMPLETED, detach(legacy), ()
    if legacy.execution_status is ExecutionStatus.TIMEOUT:
        return CollectionStatus.TIMEOUT, None, (_issue("command_timeout"),)
    if legacy.execution_status is ExecutionStatus.FAILED:
        return CollectionStatus.FAILED, None, (_issue("measurement_failed"),)
    return None


def _detach_ping(legacy):
    return PingResult(
        target=legacy.target, execution_status=legacy.execution_status,
        packets_sent=legacy.packets_sent, packets_received=legacy.packets_received,
        packet_loss_percent=legacy.packet_loss_percent, min_rtt_ms=legacy.min_rtt_ms,
        avg_rtt_ms=legacy.avg_rtt_ms, max_rtt_ms=legacy.max_rtt_ms, mdev_ms=legacy.mdev_ms,
        reachable=legacy.reachable, packet_loss_status=legacy.packet_loss_status,
        icmp_errors=legacy.icmp_errors,
    )


def _detach_dns_query(legacy):
    return DnsQueryResult(
        hostname=legacy.hostname, execution_status=legacy.execution_status,
        resolved=legacy.resolved, ip_address=legacy.ip_address,
    )


def _detach_dns_latency(legacy):
    return DnsLatencyResult(
        hostname=legacy.hostname, execution_status=legacy.execution_status,
        dns_status=legacy.dns_status, resolved=legacy.resolved,
        dns_latency_ms=legacy.dns_latency_ms, answer_count=legacy.answer_count,
    )


def _detach_dns_addresses(legacy):
    if type(legacy.ipv4_addresses) is not list or type(legacy.ipv6_addresses) is not list:
        return None
    return DnsAddressResult(
        hostname=legacy.hostname, execution_status=legacy.execution_status,
        ipv4_addresses=list(legacy.ipv4_addresses), ipv6_addresses=list(legacy.ipv6_addresses),
    )


def _interpret_ping(legacy):
    return _execution_outcome(legacy, PingResult, _detach_ping)


def _interpret_dns_query(legacy):
    return _execution_outcome(legacy, DnsQueryResult, _detach_dns_query)


def _interpret_dns_latency(legacy):
    return _execution_outcome(legacy, DnsLatencyResult, _detach_dns_latency)


def _interpret_dns_addresses(legacy):
    outcome = _execution_outcome(legacy, DnsAddressResult, _detach_dns_addresses)
    if outcome is not None and outcome[0] is CollectionStatus.COMPLETED and outcome[1] is None:
        return None
    return outcome


def _interpret_interfaces(legacy):
    if type(legacy) is not list or any(type(item) is not NetworkInterface for item in legacy):
        return None
    value = [
        NetworkInterface(
            device=item.device, interface_type=item.interface_type,
            state=item.state, connection=item.connection,
        )
        for item in legacy
    ]
    return CollectionStatus.COMPLETED, value, ()


def _interpret_sockets(legacy):
    if type(legacy) is not ListeningPortReport or type(legacy.sockets) is not list:
        return None
    if any(type(item) is not ListeningSocket for item in legacy.sockets):
        return None
    value = [
        ListeningSocket(
            protocol=item.protocol, state=item.state, local_address=item.local_address,
            local_port=item.local_port, peer_address=item.peer_address, peer_port=item.peer_port,
        )
        for item in legacy.sockets
    ]
    return CollectionStatus.COMPLETED, value, ()


def _interpret_routing(legacy):
    if type(legacy) is not RoutingSnapshot or type(legacy.errors) is not dict:
        return None
    if any(type(key) is not str or key not in _ROUTING_FIELDS for key in legacy.errors):
        return None
    if legacy.routes is not None and type(legacy.routes) is not list:
        return None

    value = RoutingSnapshot(
        hostname=legacy.hostname, interface=legacy.interface, ip_address=legacy.ip_address,
        gateway=legacy.gateway,
        routes=None if legacy.routes is None else list(legacy.routes),
        errors={},
    )
    failed_fields = [name for name in _ROUTING_FIELDS if name in legacy.errors]
    if not failed_fields:
        return CollectionStatus.COMPLETED, value, ()

    issues = [_issue("measurement_failed", name) for name in failed_fields]
    usable = any(getattr(value, name) is not None for name in _ROUTING_FIELDS)
    if not usable:
        return CollectionStatus.FAILED, None, tuple(issues)

    # Without the routing table the dependent fields were not attempted.
    if "routes" in legacy.errors:
        issues.extend(_issue("dependency_unavailable", name) for name in _ROUTING_DEPENDENTS)
    return CollectionStatus.PARTIAL, value, tuple(issues)


def _unperformed(operation_id, kind, target, status, code) -> CollectionResult:
    return CollectionResult(
        operation_id=operation_id, source=COLLECTION_SOURCES[kind], target=target,
        status=status, value=None, issues=(_issue(code),),
    )


def _attempt(operation_id, kind, target, call, interpret) -> CollectionResult:
    """Run one operation with timing and map only the expected exceptions."""
    started_at = _utc_now()
    start = _monotonic()
    legacy = None
    failure = None
    try:
        legacy = call()
    except CommandTimeoutError:
        failure = (CollectionStatus.TIMEOUT, "command_timeout")
    except CommandNotFoundError:
        failure = (CollectionStatus.UNAVAILABLE, "command_unavailable")
    except CommandError:
        failure = (CollectionStatus.FAILED, "command_failed")
    except ParsingError:
        failure = (CollectionStatus.FAILED, "parsing_failed")
    end = _monotonic()
    finished_at = _utc_now()
    timing = {"started_at": started_at, "finished_at": finished_at, "duration_seconds": end - start}

    def build(status, value, issues):
        return CollectionResult(
            operation_id=operation_id, source=COLLECTION_SOURCES[kind], target=target,
            status=status, value=value, issues=issues, **timing,
        )

    if failure is not None:
        return build(failure[0], None, (_issue(failure[1]),))

    outcome = interpret(legacy)
    if outcome is None:
        return build(*_invalid_outcome())
    try:
        return build(*outcome)
    except ValueError:
        return build(*_invalid_outcome())


def _collect(operation_id, kind, target, requested, linux, call, interpret) -> CollectionResult:
    if not requested:
        return _unperformed(operation_id, kind, target, CollectionStatus.NOT_COLLECTED, "not_requested")
    if not linux:
        return _unperformed(operation_id, kind, target, CollectionStatus.UNSUPPORTED, "unsupported_platform")
    return _attempt(operation_id, kind, target, call, interpret)


def collect_diagnostic_snapshot(
    *,
    ping_targets: tuple[str, ...] | list[str] = DEFAULT_PING_TARGETS,
    dns_hostname: str = DEFAULT_DNS_HOSTNAME,
    address_hostnames: tuple[str, ...] | list[str] = ADDRESS_LOOKUP_HOSTNAMES,
    include_routing: bool = True,
    include_interfaces: bool = True,
    include_listening_sockets: bool = True,
    include_dns_query: bool = True,
    include_dns_latency: bool = True,
) -> DiagnosticSnapshot:
    """Collect one DiagnosticSnapshot, running the operations one after another.

    Order: routing, interfaces, listening sockets, pings in the requested
    order, DNS query, DNS latency, address lookups in the requested order.
    Invalid input raises ValidationError before any command runs. On other
    platforms than Linux no command runs and requested operations are
    UNSUPPORTED. Operations that are not requested are NOT_COLLECTED.

    The default request sends ICMP packets to external hosts ("internet" and
    "google.com") and DNS requests for google.com, wikipedia.com, example.com
    and yle.fi. The include_* flags and empty ping_targets and
    address_hostnames tuples allow purely local collection.

    The snapshot holds detached copies without error text, raw output or
    analysis fields. Programming errors are never caught.
    """
    ping_targets, address_hostnames = _validate_snapshot_request(
        ping_targets, address_hostnames, dns_hostname,
        (include_routing, include_interfaces, include_listening_sockets,
         include_dns_query, include_dns_latency),
    )

    snapshot_id = _new_snapshot_id()
    started_at = _utc_now()
    start = _monotonic()
    linux = _is_linux()

    routing = _collect(
        "routing", "routing", None, include_routing, linux,
        lambda: get_routing_snapshot(), _interpret_routing,
    )
    interfaces = _collect(
        "interfaces", "interfaces", None, include_interfaces, linux,
        lambda: get_network_interfaces(), _interpret_interfaces,
    )
    listening_sockets = _collect(
        "listening_sockets", "listening_sockets", None, include_listening_sockets, linux,
        lambda: get_listening_ports(), _interpret_sockets,
    )
    pings = tuple(
        _collect(
            f"ping.{alias}", "ping", alias, True, linux,
            lambda alias=alias: run_ping_diagnostic(alias), _interpret_ping,
        )
        for alias in ping_targets
    )
    dns_query = _collect(
        "dns.query", "dns.query", dns_hostname, include_dns_query, linux,
        lambda: dns.dns_query(dns_hostname), _interpret_dns_query,
    )
    dns_latency = _collect(
        "dns.latency", "dns.latency", dns_hostname, include_dns_latency, linux,
        lambda: dns.dns_latency(dns_hostname), _interpret_dns_latency,
    )
    dns_addresses = tuple(
        _collect(
            f"dns.addresses:{hostname}", "dns.addresses", hostname, True, linux,
            lambda hostname=hostname: dns.ipv_check(hostname), _interpret_dns_addresses,
        )
        for hostname in address_hostnames
    )

    finished_at = _utc_now()
    duration_seconds = _monotonic() - start

    return DiagnosticSnapshot(
        schema_version=1, snapshot_id=snapshot_id, started_at=started_at,
        finished_at=finished_at, duration_seconds=duration_seconds,
        routing=routing, interfaces=interfaces, listening_sockets=listening_sockets,
        pings=pings, dns_query=dns_query, dns_latency=dns_latency, dns_addresses=dns_addresses,
    )
