"""Convert a DiagnosticSnapshot to JSON-compatible primitive containers.

snapshot_to_dict() returns fresh dict, list, str, int, float, bool and None
values only. It does not import json, write files, log or deserialize.

Export decisions:

- D1: RoutingSnapshot.routes (raw `ip route` lines) is intentionally omitted.
  The hostname, interface, IPv4 address and gateway are exported instead.
- D2: Every exported field is named in an explicit per-model allowlist. Legacy
  error text, raw command output, analysis text and the derived
  packet_loss_status are never exported, and an issue message is always taken
  from the controlled COLLECTION_ISSUE_MESSAGES table.
- D3: Text containing control characters is rejected by validation (a
  ValueError), never cleaned or escaped silently.

Limits of the output:

- The data is not anonymized. IP addresses, hostnames, interface and connection
  names (such as SSIDs) and socket addresses are sensitive.
- JSON escaping is not HTML, GUI, terminal or log escaping. Consumers must
  escape the text for their own output context.
- Integers beyond 2**53 - 1 and non-finite floats are rejected, never
  truncated, so json.dumps(..., allow_nan=False) works for every valid snapshot.
"""

from datetime import datetime
from enum import Enum
import math

from network_diagnostic.core.models import COLLECTION_ISSUE_MESSAGES, DiagnosticSnapshot


_MAX_SAFE_INTEGER = 2**53 - 1


def _str(value, optional=True):
    if value is None and optional:
        return None
    if type(value) is not str:
        raise ValueError("exported text must be a string")
    return value


def _bool(value):
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError("exported flag must be a bool")
    return value


def _int(value):
    if value is None:
        return None
    if type(value) is not int or abs(value) > _MAX_SAFE_INTEGER:
        raise ValueError("exported integer must be an int within the JSON safe range")
    return value


def _number(value):
    """Accept an int within the safe range or a finite float."""
    if value is None:
        return None
    if type(value) is int:
        return _int(value)
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("exported number must be a finite float or a safe int")
    return value


def _enum(value):
    if not isinstance(value, Enum):
        raise ValueError("exported status must be an enum")
    return _str(value.value, optional=False)


def _time(value):
    if not isinstance(value, datetime):
        raise ValueError("exported timestamp must be a datetime")
    return _str(value.isoformat(), optional=False)


def _text_list(values):
    if type(values) not in (list, tuple):
        raise ValueError("exported addresses must be a list")
    return [_str(item, optional=False) for item in values]


def _routing(value):
    return {
        "hostname": _str(value.hostname),
        "interface": _str(value.interface),
        "ip_address": _str(value.ip_address),
        "gateway": _str(value.gateway),
    }


def _interface(item):
    return {
        "device": _str(item.device),
        "interface_type": _str(item.interface_type),
        "state": _str(item.state),
        "connection": _str(item.connection),
    }


def _socket(item):
    return {
        "protocol": _str(item.protocol),
        "state": _str(item.state),
        "local_address": _str(item.local_address),
        "local_port": _str(item.local_port),
        "peer_address": _str(item.peer_address),
        "peer_port": _str(item.peer_port),
    }


def _ping(value):
    return {
        "target": _str(value.target, optional=False),
        "execution_status": _enum(value.execution_status),
        "packets_sent": _int(value.packets_sent),
        "packets_received": _int(value.packets_received),
        "packet_loss_percent": _number(value.packet_loss_percent),
        "min_rtt_ms": _number(value.min_rtt_ms),
        "avg_rtt_ms": _number(value.avg_rtt_ms),
        "max_rtt_ms": _number(value.max_rtt_ms),
        "mdev_ms": _number(value.mdev_ms),
        "reachable": _bool(value.reachable),
        "icmp_errors": _int(value.icmp_errors),
    }


def _dns_query(value):
    return {
        "hostname": _str(value.hostname, optional=False),
        "execution_status": _enum(value.execution_status),
        "resolved": _bool(value.resolved),
        "ip_address": _str(value.ip_address),
    }


def _dns_latency(value):
    return {
        "hostname": _str(value.hostname, optional=False),
        "execution_status": _enum(value.execution_status),
        "dns_status": _str(value.dns_status),
        "resolved": _bool(value.resolved),
        "dns_latency_ms": _number(value.dns_latency_ms),
        "answer_count": _int(value.answer_count),
    }


def _dns_addresses(value):
    return {
        "hostname": _str(value.hostname, optional=False),
        "execution_status": _enum(value.execution_status),
        "ipv4_addresses": _text_list(value.ipv4_addresses),
        "ipv6_addresses": _text_list(value.ipv6_addresses),
    }


def _issue(issue):
    code = _str(issue.code, optional=False)
    return {
        "code": code,
        "message": COLLECTION_ISSUE_MESSAGES[code],
        "field": _str(issue.field),
    }


def _value(result):
    value = result.value
    if value is None:
        return None
    operation_id = result.operation_id
    if operation_id == "routing":
        return _routing(value)
    if operation_id == "interfaces":
        return [_interface(item) for item in value]
    if operation_id == "listening_sockets":
        return [_socket(item) for item in value]
    if operation_id.startswith("ping."):
        return _ping(value)
    if operation_id == "dns.query":
        return _dns_query(value)
    if operation_id == "dns.latency":
        return _dns_latency(value)
    return _dns_addresses(value)


def _result(result):
    return {
        "operation_id": _str(result.operation_id, optional=False),
        "source": _str(result.source, optional=False),
        "target": _str(result.target),
        "status": _enum(result.status),
        "value": _value(result),
        "issues": [_issue(issue) for issue in result.issues],
        "started_at": None if result.started_at is None else _time(result.started_at),
        "finished_at": None if result.finished_at is None else _time(result.finished_at),
        "duration_seconds": _number(result.duration_seconds),
    }


def snapshot_to_dict(snapshot: DiagnosticSnapshot) -> dict:
    """Return the snapshot as new primitive containers.

    Raises ValueError when the snapshot or any nested value is invalid.
    """
    if type(snapshot) is not DiagnosticSnapshot:
        raise ValueError("snapshot must be a DiagnosticSnapshot")
    snapshot.validate()

    return {
        "schema_version": _int(snapshot.schema_version),
        "snapshot_id": _str(snapshot.snapshot_id, optional=False),
        "started_at": _time(snapshot.started_at),
        "finished_at": _time(snapshot.finished_at),
        "duration_seconds": _number(snapshot.duration_seconds),
        "routing": _result(snapshot.routing),
        "interfaces": _result(snapshot.interfaces),
        "listening_sockets": _result(snapshot.listening_sockets),
        "pings": [_result(result) for result in snapshot.pings],
        "dns_query": _result(snapshot.dns_query),
        "dns_latency": _result(snapshot.dns_latency),
        "dns_addresses": [_result(result) for result in snapshot.dns_addresses],
    }
