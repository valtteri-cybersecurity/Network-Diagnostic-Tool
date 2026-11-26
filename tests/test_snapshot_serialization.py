import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from network_diagnostic.core.models import (
    COLLECTION_ISSUE_MESSAGES, CollectionIssue, CollectionResult, CollectionStatus,
    DiagnosticSnapshot, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    LatencyAnalysis, ListeningSocket, NetworkInterface, PacketLossStatus, PingResult, RoutingSnapshot,
)
from network_diagnostic.core import snapshot_serialization
from network_diagnostic.core.snapshot_serialization import snapshot_to_dict

S = CollectionStatus
COMPLETE = ExecutionStatus.COMPLETED
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=2, microseconds=500)
UUID4 = "3f2b8c1e-9d4a-4b6e-8f10-2a7c5d9e1b34"
MAX_SAFE = 2**53 - 1

RESULT_KEYS = [
    "operation_id", "source", "target", "status", "value", "issues",
    "started_at", "finished_at", "duration_seconds",
]
SNAPSHOT_KEYS = [
    "schema_version", "snapshot_id", "started_at", "finished_at", "duration_seconds", "routing",
    "interfaces", "listening_sockets", "pings", "dns_query", "dns_latency", "dns_addresses",
]


def timing():
    return {"started_at": T0, "finished_at": T1, "duration_seconds": 2.0005}


def done(operation_id, source, target, value):
    return CollectionResult(operation_id, source, target, S.COMPLETED, value, **timing())


def ping(alias="gateway", **fields):
    values = {
        "target": alias, "execution_status": COMPLETE, "packets_sent": 4, "packets_received": 3,
        "packet_loss_percent": 25.0, "min_rtt_ms": 1.0, "avg_rtt_ms": 2.5, "max_rtt_ms": 4.0, "mdev_ms": 0.5,
        "reachable": True, "icmp_errors": 0, "packet_loss_status": PacketLossStatus.PARTIAL_LOSS,
        "error": "raw error", "raw_output": "raw output", "latency_analysis": "OK", "rtt_range_analysis": "Good",
    }
    values.update(fields)
    return done(f"ping.{alias}", "icmp_echo", alias, PingResult(**values))


def make_snapshot(**overrides):
    values = {
        "schema_version": 1, "snapshot_id": UUID4, "started_at": T0, "finished_at": T1, "duration_seconds": 2.5,
        "routing": done("routing", "linux_routing", None, RoutingSnapshot(
            hostname="host", interface="eth0", ip_address="192.168.1.5", gateway="192.168.1.1",
            routes=["default via 192.168.1.1 dev eth0"],
        )),
        "interfaces": done("interfaces", "network_manager", None, [
            NetworkInterface("eth0", "ethernet", "connected", "Wired"), NetworkInterface("lo"),
        ]),
        "listening_sockets": done("listening_sockets", "socket_table", None, [
            ListeningSocket("tcp", "LISTEN", "127.0.0.1", "22", "0.0.0.0", "*"), ListeningSocket(),
        ]),
        "pings": (ping("gateway"), ping("localhost")),
        "dns_query": done("dns.query", "system_resolver", "example.com", DnsQueryResult(
            "example.com", COMPLETE, True, "93.184.216.34", error="raw",
        )),
        "dns_latency": done("dns.latency", "dns_response", "example.com", DnsLatencyResult(
            "example.com", COMPLETE, "NOERROR", True, 12.5, 1, error="raw",
            latency_analysis=LatencyAnalysis(12.5, "OK"),
        )),
        "dns_addresses": (
            done("dns.addresses:b.example", "system_resolver", "b.example", DnsAddressResult(
                "b.example", COMPLETE, ["1.1.1.1", "9.9.9.9"], ["2001:db8::1"], error="raw",
            )),
            done("dns.addresses:a.example", "system_resolver", "a.example", DnsAddressResult(
                "a.example", COMPLETE, [], [],
            )),
        ),
    }
    values.update(overrides)
    return DiagnosticSnapshot(**values)


def issue(code, field=None):
    return CollectionIssue(code, COLLECTION_ISSUE_MESSAGES[code], field)


def unperformed(operation_id, source, target, status, code):
    return CollectionResult(operation_id, source, target, status, issues=(issue(code),))


# --- Structure -----------------------------------------------------------------


def test_snapshot_and_result_keys_are_exact_and_ordered():
    data = snapshot_to_dict(make_snapshot())

    assert list(data) == SNAPSHOT_KEYS
    for section in (data["routing"], data["interfaces"], data["listening_sockets"], data["dns_query"],
                    data["dns_latency"], *data["pings"], *data["dns_addresses"]):
        assert list(section) == RESULT_KEYS
    assert data["schema_version"] == 1 and data["snapshot_id"] == UUID4
    assert data["started_at"] == "2026-01-01T12:00:00+00:00"
    assert data["finished_at"] == "2026-01-01T12:00:02.000500+00:00"
    assert data["duration_seconds"] == 2.5


def test_value_models_have_exact_key_sets_in_order():
    data = snapshot_to_dict(make_snapshot())

    assert list(data["routing"]["value"]) == ["hostname", "interface", "ip_address", "gateway"]
    assert list(data["interfaces"]["value"][0]) == ["device", "interface_type", "state", "connection"]
    assert list(data["listening_sockets"]["value"][0]) == [
        "protocol", "state", "local_address", "local_port", "peer_address", "peer_port",
    ]
    assert list(data["pings"][0]["value"]) == [
        "target", "execution_status", "packets_sent", "packets_received", "packet_loss_percent",
        "min_rtt_ms", "avg_rtt_ms", "max_rtt_ms", "mdev_ms", "reachable", "icmp_errors",
    ]
    assert list(data["dns_query"]["value"]) == ["hostname", "execution_status", "resolved", "ip_address"]
    assert list(data["dns_latency"]["value"]) == [
        "hostname", "execution_status", "dns_status", "resolved", "dns_latency_ms", "answer_count",
    ]
    assert list(data["dns_addresses"][0]["value"]) == [
        "hostname", "execution_status", "ipv4_addresses", "ipv6_addresses",
    ]


def test_values_are_exported_with_exact_content():
    data = snapshot_to_dict(make_snapshot())

    assert data["routing"]["value"] == {
        "hostname": "host", "interface": "eth0", "ip_address": "192.168.1.5", "gateway": "192.168.1.1",
    }
    assert data["interfaces"]["value"][1] == {
        "device": "lo", "interface_type": None, "state": None, "connection": None,
    }
    assert data["listening_sockets"]["value"][0]["local_port"] == "22"
    assert data["pings"][0]["value"] == {
        "target": "gateway", "execution_status": "completed", "packets_sent": 4, "packets_received": 3,
        "packet_loss_percent": 25.0, "min_rtt_ms": 1.0, "avg_rtt_ms": 2.5, "max_rtt_ms": 4.0, "mdev_ms": 0.5,
        "reachable": True, "icmp_errors": 0,
    }
    assert data["dns_query"]["value"]["ip_address"] == "93.184.216.34"
    assert data["dns_latency"]["value"]["answer_count"] == 1
    assert data["dns_addresses"][0]["value"]["ipv4_addresses"] == ["1.1.1.1", "9.9.9.9"]


def test_order_of_lists_and_groups_is_preserved():
    data = snapshot_to_dict(make_snapshot())

    assert [p["target"] for p in data["pings"]] == ["gateway", "localhost"]
    assert [a["target"] for a in data["dns_addresses"]] == ["b.example", "a.example"]
    assert [i["device"] for i in data["interfaces"]["value"]] == ["eth0", "lo"]
    assert data["dns_addresses"][0]["value"]["ipv4_addresses"] == ["1.1.1.1", "9.9.9.9"]


def test_excluded_fields_are_absent_everywhere():
    text = json.dumps(snapshot_to_dict(make_snapshot()))

    for key in ("routes", "errors", "error", "raw_output", "latency_analysis", "rtt_range_analysis",
                "packet_loss_status", "services", "service_name", "counts"):
        assert f'"{key}"' not in text
    for leaked in ("raw error", "raw output", "default via"):
        assert leaked not in text


def test_pings_and_addresses_are_lists_and_empty_groups_are_empty_lists():
    data = snapshot_to_dict(make_snapshot(pings=(), dns_addresses=()))

    assert data["pings"] == [] and data["dns_addresses"] == []


@pytest.mark.parametrize("status", list(S))
def test_every_collection_status_is_serialized_by_value(status):
    issues_code = {
        S.PARTIAL: "command_failed", S.FAILED: "command_failed", S.TIMEOUT: "command_timeout",
        S.UNAVAILABLE: "command_unavailable", S.UNSUPPORTED: "unsupported_platform",
        S.NOT_COLLECTED: "not_requested",
    }
    if status is S.COMPLETED:
        result = done("interfaces", "network_manager", None, [])
    elif status is S.PARTIAL:
        result = CollectionResult(
            "routing", "linux_routing", None, status, RoutingSnapshot(hostname="h"),
            (issue("command_failed", "gateway"),), **timing(),
        )
    else:
        extra = {} if status in (S.UNSUPPORTED, S.NOT_COLLECTED) else timing()
        result = CollectionResult(
            "routing", "linux_routing", None, status, issues=(issue(issues_code[status]),), **extra,
        )

    data = snapshot_to_dict(make_snapshot(routing=result) if result.operation_id == "routing"
                            else make_snapshot(interfaces=result))

    section = data["routing"] if result.operation_id == "routing" else data["interfaces"]
    assert section["status"] == status.value
    assert (section["value"] is None) == (status not in (S.COMPLETED, S.PARTIAL))


def test_none_values_and_missing_timing_are_preserved():
    snapshot = make_snapshot(
        routing=unperformed("routing", "linux_routing", None, S.NOT_COLLECTED, "not_requested"),
        dns_query=unperformed("dns.query", "system_resolver", "example.com", S.UNSUPPORTED, "unsupported_platform"),
    )

    data = snapshot_to_dict(snapshot)

    for section in (data["routing"], data["dns_query"]):
        assert section["value"] is None
        assert (section["started_at"], section["finished_at"], section["duration_seconds"]) == (None, None, None)
    assert data["routing"]["target"] is None and data["dns_query"]["target"] == "example.com"


def test_issue_message_comes_from_the_controlled_table_and_field_is_kept():
    result = CollectionResult(
        "routing", "linux_routing", None, S.PARTIAL, RoutingSnapshot(hostname="h"),
        (issue("command_failed", "gateway"), issue("dependency_unavailable", "interface")), **timing(),
    )

    data = snapshot_to_dict(make_snapshot(routing=result))

    assert data["routing"]["issues"] == [
        {"code": "command_failed", "message": COLLECTION_ISSUE_MESSAGES["command_failed"], "field": "gateway"},
        {"code": "dependency_unavailable", "message": COLLECTION_ISSUE_MESSAGES["dependency_unavailable"],
         "field": "interface"},
    ]
    assert list(data["routing"]["issues"][0]) == ["code", "message", "field"]


def test_issue_message_is_not_taken_from_a_mutated_issue_object():
    snapshot = make_snapshot(
        interfaces=unperformed("interfaces", "network_manager", None, S.NOT_COLLECTED, "not_requested"),
    )
    snapshot.interfaces.issues[0].message = "tampered"

    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)


# --- JSON compatibility --------------------------------------------------------------


@pytest.mark.parametrize("snapshot", [
    make_snapshot(),
    make_snapshot(pings=(), dns_addresses=()),
    make_snapshot(pings=(ping("internet", packet_loss_percent=100.0, packets_received=0, min_rtt_ms=None,
                              avg_rtt_ms=None, max_rtt_ms=None, mdev_ms=None, reachable=False),)),
    make_snapshot(duration_seconds=0, started_at=T1, finished_at=T0),
], ids=["full", "empty_groups", "total_loss", "zero_duration_reversed_clock"])
def test_result_round_trips_through_strict_json(snapshot):
    data = snapshot_to_dict(snapshot)

    assert json.loads(json.dumps(data, allow_nan=False)) == data


def test_unsafe_text_is_preserved_verbatim_because_json_is_not_html_escaping():
    markup = "<script>alert(1)</script> & \"quoted\" $(id) \\ é"
    snapshot = make_snapshot(interfaces=done("interfaces", "network_manager", None, [NetworkInterface(
        device="eth0", connection=markup,
    )]))

    data = snapshot_to_dict(snapshot)

    assert data["interfaces"]["value"][0]["connection"] == markup
    assert json.loads(json.dumps(data))["interfaces"]["value"][0]["connection"] == markup


@pytest.mark.parametrize("char", ["\x00", "\x1b", "\x7f", "\x85", "\n"])
def test_control_characters_are_rejected_not_cleaned(char):
    with pytest.raises(ValueError):
        make_snapshot(interfaces=done("interfaces", "network_manager", None, [NetworkInterface(
            connection="Wi" + char + "Fi")]))


# --- Safe integers and mutation -------------------------------------------------------


@pytest.mark.parametrize("field", ["packets_sent", "icmp_errors"])
def test_integers_up_to_two_to_the_53_minus_one_are_accepted(field):
    values = {"packets_received": 0, field: MAX_SAFE}
    data = snapshot_to_dict(make_snapshot(pings=(ping("internet", **values),)))

    assert data["pings"][0]["value"][field] == MAX_SAFE


@pytest.mark.parametrize("field", ["packets_sent", "icmp_errors"])
def test_integers_above_the_safe_range_are_rejected_not_truncated(field):
    snapshot = make_snapshot(pings=(ping("internet", packets_received=0, **{field: MAX_SAFE}),))
    setattr(snapshot.pings[0].value, field, MAX_SAFE + 1)

    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)


def test_answer_count_and_schema_version_stay_exact_ints():
    data = snapshot_to_dict(make_snapshot())

    assert type(data["schema_version"]) is int and type(data["dns_latency"]["value"]["answer_count"]) is int


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: setattr(s.pings[0].value, "avg_rtt_ms", math.nan),
        lambda s: setattr(s.pings[0].value, "avg_rtt_ms", math.inf),
        lambda s: setattr(s.dns_latency.value, "dns_latency_ms", -math.inf),
        lambda s: setattr(s, "duration_seconds", math.nan),
        lambda s: setattr(s.routing, "value", NetworkInterface()),
        lambda s: setattr(s.interfaces, "value", [NetworkInterface(), "eth0"]),
        lambda s: setattr(s.pings[0].value, "execution_status", "completed"),
        lambda s: setattr(s.dns_query.value, "hostname", StrText("example.com")),
        lambda s: setattr(s.routing.value, "hostname", StrText("host")),
        lambda s: setattr(s.pings[0].value, "packets_sent", True),
        lambda s: setattr(s.listening_sockets.value[0], "local_port", 22),
        lambda s: setattr(s.routing.value, "hostname", "bad\x00"),
        lambda s: setattr(s, "snapshot_id", StrText(UUID4)),
        lambda s: setattr(s.dns_addresses[0].value, "ipv4_addresses", ["1.1.1.1", None]),
        lambda s: setattr(s.dns_addresses[0], "value", None),
        lambda s: setattr(s, "pings", list(s.pings)),
    ],
    ids=range(16),
)
def test_snapshot_mutated_after_construction_is_rejected(mutate):
    snapshot = make_snapshot()
    mutate(snapshot)

    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)


class StrText(str):
    pass


@pytest.mark.parametrize("bad", [None, {}, "snapshot", make_snapshot().routing])
def test_non_snapshot_arguments_are_rejected(bad):
    with pytest.raises(ValueError):
        snapshot_to_dict(bad)


class SnapshotSubclass(DiagnosticSnapshot):
    pass


def test_snapshot_subclass_is_rejected():
    values = {name: getattr(make_snapshot(), name) for name in SNAPSHOT_KEYS}

    with pytest.raises(ValueError):
        snapshot_to_dict(SnapshotSubclass(**values))


def test_float_valued_integers_and_safe_int_durations_are_serialized_as_given():
    data = snapshot_to_dict(make_snapshot(duration_seconds=3))

    assert data["duration_seconds"] == 3 and type(data["duration_seconds"]) is int


# --- Independence -----------------------------------------------------------------------


def test_mutating_the_returned_dict_does_not_change_the_snapshot_or_later_exports():
    snapshot = make_snapshot()
    first = snapshot_to_dict(snapshot)

    first["routing"]["value"]["hostname"] = "tampered"
    first["interfaces"]["value"].clear()
    first["pings"].append("x")
    first["dns_addresses"][0]["value"]["ipv4_addresses"].append("6.6.6.6")
    first["routing"]["issues"].append("x")

    assert snapshot.routing.value.hostname == "host"
    assert len(snapshot.interfaces.value) == 2
    assert len(snapshot.pings) == 2
    assert snapshot.dns_addresses[0].value.ipv4_addresses == ["1.1.1.1", "9.9.9.9"]
    snapshot.validate()
    assert snapshot_to_dict(snapshot) == snapshot_to_dict(make_snapshot())


def collect_containers(node, found):
    if isinstance(node, (dict, list)):
        found.append(node)
        for child in (node.values() if isinstance(node, dict) else node):
            collect_containers(child, found)
    return found


def test_two_exports_share_no_mutable_containers():
    snapshot = make_snapshot()

    first = collect_containers(snapshot_to_dict(snapshot), [])
    second = collect_containers(snapshot_to_dict(snapshot), [])

    assert first and len(first) == len(second)
    assert not {id(item) for item in first} & {id(item) for item in second}


def test_export_shares_no_containers_with_the_snapshot_values():
    snapshot = make_snapshot()
    data = snapshot_to_dict(snapshot)

    assert data["interfaces"]["value"] is not snapshot.interfaces.value
    assert (data["dns_addresses"][0]["value"]["ipv4_addresses"]
            is not snapshot.dns_addresses[0].value.ipv4_addresses)


def test_module_exposes_only_the_documented_entry_point():
    assert callable(snapshot_serialization.snapshot_to_dict)
    assert not hasattr(snapshot_serialization, "json")
    assert not hasattr(snapshot_serialization, "open")


def test_none_flags_numbers_and_counts_are_exported_as_none():
    empty_ping = PingResult(target="gateway", execution_status=COMPLETE)
    data = snapshot_to_dict(make_snapshot(pings=(done("ping.gateway", "icmp_echo", "gateway", empty_ping),)))

    value = data["pings"][0]["value"]
    assert [value[key] for key in list(value)[2:]] == [None] * 9


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: setattr(s.pings[0].value, "reachable", 1),
        lambda s: setattr(s.pings[0].value, "avg_rtt_ms", math.nan),
        lambda s: setattr(s.pings[0].value, "avg_rtt_ms", "1.0"),
        lambda s: setattr(s.pings[0].value, "execution_status", "completed"),
        lambda s: setattr(s.pings[0], "started_at", "2026-01-01"),
        lambda s: setattr(s.dns_addresses[0].value, "ipv4_addresses", "1.1.1.1"),
        lambda s: setattr(s.dns_addresses[0].value, "ipv6_addresses", None),
        lambda s: setattr(s, "snapshot_id", 5),
        lambda s: setattr(s.pings[0].value, "packets_sent", MAX_SAFE + 1),
    ],
    ids=range(9),
)
def test_primitive_checks_reject_bad_values_even_if_model_validation_is_bypassed(monkeypatch, mutate):
    snapshot = make_snapshot()
    mutate(snapshot)
    monkeypatch.setattr(DiagnosticSnapshot, "validate", lambda self: None)

    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)


def test_unicode_formatting_characters_are_exported_verbatim_by_policy():
    # D3 policy: U+202E and U+2028 are not control characters under the current rule.
    text = "Wi\u202eFi\u2028"
    snapshot = make_snapshot(interfaces=done("interfaces", "network_manager", None, [NetworkInterface(
        connection=text)]))

    data = snapshot_to_dict(snapshot)

    assert data["interfaces"]["value"][0]["connection"] == text
    assert json.loads(json.dumps(data, allow_nan=False))["interfaces"]["value"][0]["connection"] == text


class BadIsoformat(datetime):
    def isoformat(self, *arguments, **keywords):
        return 5


def test_timestamp_text_must_be_a_str_even_for_a_datetime_subclass(monkeypatch):
    snapshot = make_snapshot()
    snapshot.pings[0].started_at = BadIsoformat(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(DiagnosticSnapshot, "validate", lambda self: None)

    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)


def test_duration_above_the_safe_range_is_rejected_by_validation_and_by_the_serializer(monkeypatch):
    snapshot = make_snapshot(duration_seconds=MAX_SAFE)
    assert snapshot_to_dict(snapshot)["duration_seconds"] == MAX_SAFE
    snapshot.duration_seconds = MAX_SAFE + 1
    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)
    monkeypatch.setattr(DiagnosticSnapshot, "validate", lambda self: None)
    with pytest.raises(ValueError):
        snapshot_to_dict(snapshot)
