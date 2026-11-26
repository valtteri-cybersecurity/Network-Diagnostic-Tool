from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Generic, Optional, TypeVar
from uuid import UUID


@dataclass
class CommandResult:
    arguments: list[str]
    stdout: str
    stderr: str
    return_code: int
    duration_seconds: float


class ExecutionStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class PacketLossStatus(Enum):
    UNAVAILABLE = "unavailable"
    NO_LOSS = "no_loss"
    PARTIAL_LOSS = "partial_loss"
    TOTAL_LOSS = "total_loss"
    INVALID = "invalid"


@dataclass
class PingResult:
    target: str = ""
    execution_status: ExecutionStatus = ExecutionStatus.FAILED
    packets_sent: Optional[int] = None
    packets_received: Optional[int] = None
    packet_loss_percent: Optional[float] = None
    min_rtt_ms: Optional[float] = None
    avg_rtt_ms: Optional[float] = None
    max_rtt_ms: Optional[float] = None
    mdev_ms: Optional[float] = None
    reachable: Optional[bool] = None
    error: Optional[str] = None
    raw_output: str = ""
    packet_loss_status: PacketLossStatus = PacketLossStatus.UNAVAILABLE
    latency_analysis: Optional[str] = None
    rtt_range_analysis: Optional[str] = None
    # ICMP error messages such as "Destination Host Unreachable" (`+N errors`).
    # None means that ping did not report an error count.
    icmp_errors: Optional[int] = None


@dataclass
class DnsQueryResult:
    hostname: str = ""
    execution_status: ExecutionStatus = ExecutionStatus.FAILED
    resolved: Optional[bool] = None
    ip_address: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DnsLatencyResult:
    hostname: str = ""
    execution_status: ExecutionStatus = ExecutionStatus.FAILED
    dns_status: Optional[str] = None
    resolved: Optional[bool] = None
    dns_latency_ms: Optional[float] = None
    answer_count: Optional[int] = None
    error: Optional[str] = None
    # Filled by the service layer only when a latency was measured.
    latency_analysis: Optional["LatencyAnalysis"] = None


@dataclass
class DnsAddressResult:
    hostname: str = ""
    execution_status: ExecutionStatus = ExecutionStatus.FAILED
    ipv4_addresses: list[str] = field(default_factory=list)
    ipv6_addresses: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ListeningSocket:
    protocol: Optional[str] = None
    state: Optional[str] = None
    local_address: Optional[str] = None
    local_port: Optional[str] = None
    peer_address: Optional[str] = None
    peer_port: Optional[str] = None


@dataclass
class SocketCounts:
    tcp_count: int = 0
    udp_count: int = 0


@dataclass
class ServiceInfo:
    protocol: Optional[str] = None
    local_address: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None


@dataclass
class NetworkInterface:
    device: Optional[str] = None
    interface_type: Optional[str] = None
    state: Optional[str] = None
    connection: Optional[str] = None


@dataclass
class LatencyAnalysis:
    latency_ms: float
    status: str


@dataclass
class ListeningPortReport:
    """Listening TCP and unconnected UDP sockets from one `ss` run."""
    sockets: list[ListeningSocket] = field(default_factory=list)
    counts: SocketCounts = field(default_factory=SocketCounts)
    services: list[ServiceInfo] = field(default_factory=list)


@dataclass
class RoutingSnapshot:
    """Hostname and default-route details with errors of the parts that failed.

    `routes` is None when the routing table could not be read and an empty list
    when it has no routes. `errors` maps a field name to its error message; a
    field with an error, or one depending on an unavailable routing table, is None.
    """
    hostname: Optional[str] = None
    interface: Optional[str] = None
    ip_address: Optional[str] = None
    gateway: Optional[str] = None
    routes: Optional[list[str]] = None
    errors: dict[str, str] = field(default_factory=dict)


class CollectionStatus(Enum):
    """Collection availability, independent of the observed network condition."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    NOT_COLLECTED = "not_collected"


COLLECTION_ISSUE_MESSAGES = {
    "not_requested": "Diagnostic operation was not requested.",
    "unsupported_platform": "Diagnostic operation is supported only on Linux.",
    "command_unavailable": "Required diagnostic command is unavailable.",
    "command_timeout": "Diagnostic command timed out.",
    "command_failed": "Diagnostic command failed.",
    "parsing_failed": "Diagnostic output could not be parsed.",
    "measurement_failed": "Diagnostic measurement did not complete.",
    "dependency_unavailable": "A required observation is unavailable.",
    "invalid_result": "Diagnostic operation returned invalid data.",
}

# These labels identify data sources, never executable commands or callbacks.
COLLECTION_SOURCES = {
    "routing": "linux_routing",
    "interfaces": "network_manager",
    "listening_sockets": "socket_table",
    "ping": "icmp_echo",
    "dns.query": "system_resolver",
    "dns.latency": "dns_response",
    "dns.addresses": "system_resolver",
}
_PING_TARGETS = ("localhost", "gateway", "internet", "google.com")
_ROUTING_FIELDS = ("hostname", "interface", "ip_address", "gateway", "routes")
_FAILURE_CODES = ("command_failed", "parsing_failed", "measurement_failed", "invalid_result")
_STATUS_ISSUE_CODES = {
    CollectionStatus.COMPLETED: (),
    CollectionStatus.PARTIAL: _FAILURE_CODES + (
        "command_timeout", "command_unavailable", "dependency_unavailable",
    ),
    CollectionStatus.FAILED: _FAILURE_CODES,
    CollectionStatus.TIMEOUT: ("command_timeout",),
    CollectionStatus.UNAVAILABLE: ("command_unavailable", "dependency_unavailable"),
    CollectionStatus.UNSUPPORTED: ("unsupported_platform",),
    CollectionStatus.NOT_COLLECTED: ("not_requested",),
}
_T = TypeVar("_T")


def _validate_utc(value: datetime) -> None:
    # Only datetime.timezone.utc: a zero offset alone also matches zones such as
    # Europe/London in winter, which are not UTC.
    if not isinstance(value, datetime) or value.tzinfo is not timezone.utc:
        raise ValueError("timestamps must be timezone-aware UTC datetimes")


def _validate_duration(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("duration must be a finite non-negative number")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("duration must be a finite non-negative number")


@dataclass
class CollectionIssue:
    """Controlled collection error, without command output or exception objects."""

    code: str
    message: str
    field: Optional[str] = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Recheck mutable metadata; invalid model contracts raise ValueError."""
        if type(self.code) is not str or self.code not in COLLECTION_ISSUE_MESSAGES:
            raise ValueError("unknown collection issue code")
        if type(self.message) is not str or self.message != COLLECTION_ISSUE_MESSAGES[self.code]:
            raise ValueError("collection issue message must match its controlled code")
        if self.field is not None and (type(self.field) is not str or self.field not in _ROUTING_FIELDS):
            raise ValueError("collection issue field must name a routing field")


@dataclass
class CollectionResult(Generic[_T]):
    """Caller-owned result envelope; values are not copied or deeply frozen.

    COMPLETED has a value and no issues. PARTIAL has a value and identified
    failed fields. All other statuses have no value and explanatory issues.
    Empty collections, zero and False are values, not missing observations.

    Attempted operations have all three timing fields; NOT_COLLECTED and
    UNSUPPORTED have none. UNAVAILABLE permits either complete timing or none.
    Wall-clock timestamps may reverse when the system clock changes; duration
    is supplied independently from a monotonic clock and is never inferred.

    Target checks enforce only bounded, non-option ASCII structure. A future
    collector must also use the existing semantic hostname/IP validation.
    """

    operation_id: str
    source: str
    target: Optional[str]
    status: CollectionStatus
    value: Optional[_T] = None
    issues: tuple[CollectionIssue, ...] = ()
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate the envelope, not all fields of legacy observation models."""
        if not isinstance(self.status, CollectionStatus):
            raise ValueError("status must be a CollectionStatus")
        if type(self.operation_id) is not str:
            raise ValueError("operation_id must identify a supported diagnostic operation")
        if self.operation_id in ("routing", "interfaces", "listening_sockets"):
            kind = self.operation_id
            if self.target is not None:
                raise ValueError("local diagnostic operations have no target")
        elif self.operation_id.startswith("ping."):
            kind = "ping"
            if type(self.target) is not str or self.target not in _PING_TARGETS or self.operation_id != f"ping.{self.target}":
                raise ValueError("ping operation_id and predefined target must match")
        else:
            if (
                type(self.target) is not str
                or re.fullmatch(r"[A-Za-z0-9.:-]{1,254}", self.target) is None
                or self.target.startswith("-")
            ):
                raise ValueError("DNS target must have bounded non-option ASCII structure")
            if self.operation_id in ("dns.query", "dns.latency"):
                kind = self.operation_id
            elif self.operation_id == f"dns.addresses:{self.target}":
                kind = "dns.addresses"
            else:
                raise ValueError("operation_id must identify a supported diagnostic operation")
        if type(self.source) is not str or self.source != COLLECTION_SOURCES[kind]:
            raise ValueError("source must match the diagnostic operation")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, CollectionIssue) for issue in self.issues
        ):
            raise ValueError("issues must be a tuple of CollectionIssue objects")
        for issue in self.issues:
            issue.validate()
            if issue.code not in _STATUS_ISSUE_CODES[self.status]:
                raise ValueError("collection issue code contradicts collection status")
            if issue.field is not None and kind != "routing":
                raise ValueError("issue fields are defined only for the routing operation")
            if self.status is CollectionStatus.PARTIAL and issue.field is None:
                raise ValueError("partial results must identify failed fields")
        if self.status is CollectionStatus.COMPLETED:
            if self.value is None or self.issues:
                raise ValueError("completed results require a value and no issues")
        elif self.status is CollectionStatus.PARTIAL:
            if self.value is None or not self.issues:
                raise ValueError("partial results require a value and identified issues")
        elif self.value is not None or not self.issues:
            raise ValueError("unsuccessful or uncollected results require no value and explanatory issues")

        timing = (self.started_at, self.finished_at, self.duration_seconds)
        if all(value is None for value in timing):
            if self.status not in (
                CollectionStatus.UNAVAILABLE, CollectionStatus.UNSUPPORTED, CollectionStatus.NOT_COLLECTED,
            ):
                raise ValueError("attempted operations require all timing fields")
        else:
            if any(value is None for value in timing):
                raise ValueError("timing fields must be all present or all absent")
            if self.status in (CollectionStatus.UNSUPPORTED, CollectionStatus.NOT_COLLECTED):
                raise ValueError("unperformed operations must not have timing fields")
            _validate_utc(self.started_at)
            _validate_utc(self.finished_at)
            _validate_duration(self.duration_seconds)


def _validate_snapshot_section(result, operation_id, value_type, item_type=None) -> None:
    if not isinstance(result, CollectionResult):
        raise ValueError("snapshot sections must be CollectionResult objects")
    result.validate()
    if result.operation_id != operation_id:
        raise ValueError("snapshot section has the wrong operation_id")
    if result.value is not None:
        if not isinstance(result.value, value_type):
            raise ValueError("snapshot section has the wrong observation type")
        if item_type is not None and not all(isinstance(item, item_type) for item in result.value):
            raise ValueError("snapshot list contains the wrong observation type")


@dataclass
class DiagnosticSnapshot:
    """Schema 1 observations over a collection interval, independent of any UI.

    UUID4 identifiers, UTC timestamps and monotonic durations are supplied by
    the caller, never generated by constructing this model. Wall-clock times
    may reverse. Results preserve requested order; targets must be unique.

    This is a mutable, caller-owned model, with no automatic defensive copies.
    A future collector must detach legacy results. A future serializer must
    revalidate the envelope and nested observation payloads, return fresh
    primitive containers, and reject non-finite measurements. Its explicit
    allowlist excludes legacy errors, raw_output, stdout/stderr, arguments,
    analysis fields and service-name guesses. This class is not an exporter,
    executable action or proof of deep immutability/integrity.
    """

    schema_version: int
    snapshot_id: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    routing: CollectionResult[RoutingSnapshot]
    interfaces: CollectionResult[list[NetworkInterface]]
    listening_sockets: CollectionResult[list[ListeningSocket]]
    pings: tuple[CollectionResult[PingResult], ...]
    dns_query: CollectionResult[DnsQueryResult]
    dns_latency: CollectionResult[DnsLatencyResult]
    dns_addresses: tuple[CollectionResult[DnsAddressResult], ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Recheck schema and section contracts without reinterpreting observations."""
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("snapshot schema_version must be 1")
        if not isinstance(self.snapshot_id, str):
            raise ValueError("snapshot_id must be a canonical UUID4 string")
        try:
            identifier = UUID(self.snapshot_id)
        except ValueError as error:
            raise ValueError("snapshot_id must be a canonical UUID4 string") from error
        if identifier.version != 4 or str(identifier) != self.snapshot_id:
            raise ValueError("snapshot_id must be a canonical UUID4 string")
        _validate_utc(self.started_at)
        _validate_utc(self.finished_at)
        _validate_duration(self.duration_seconds)
        _validate_snapshot_section(self.routing, "routing", RoutingSnapshot)
        _validate_snapshot_section(self.interfaces, "interfaces", list, NetworkInterface)
        _validate_snapshot_section(self.listening_sockets, "listening_sockets", list, ListeningSocket)
        _validate_snapshot_section(self.dns_query, "dns.query", DnsQueryResult)
        _validate_snapshot_section(self.dns_latency, "dns.latency", DnsLatencyResult)
        for results, kind, value_type in (
            (self.pings, "ping", PingResult),
            (self.dns_addresses, "dns.addresses", DnsAddressResult),
        ):
            if not isinstance(results, tuple) or len(results) > 4:
                raise ValueError("snapshot target groups must be tuples with at most four results")
            targets = []
            for result in results:
                if not isinstance(result, CollectionResult):
                    raise ValueError("snapshot target groups must contain CollectionResult objects")
                operation_id = f"ping.{result.target}" if kind == "ping" else f"dns.addresses:{result.target}"
                _validate_snapshot_section(result, operation_id, value_type)
                if result.target in targets:
                    raise ValueError("snapshot target groups must not contain duplicate targets")
                targets.append(result.target)
