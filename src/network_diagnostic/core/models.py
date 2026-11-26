from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
