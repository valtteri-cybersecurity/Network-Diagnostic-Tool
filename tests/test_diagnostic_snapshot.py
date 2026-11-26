import math
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from network_diagnostic.core.models import (
    COLLECTION_ISSUE_MESSAGES, CollectionIssue, CollectionResult, CollectionStatus,
    DiagnosticSnapshot, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    ListeningSocket, NetworkInterface, PacketLossStatus, PingResult, RoutingSnapshot,
)

S = CollectionStatus
UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=2)
UUID4 = "3f2b8c1e-9d4a-4b6e-8f10-2a7c5d9e1b34"
UUID1 = "6fa459ea-ee8a-11e2-8f10-2a7c5d9e1b34"

FAILURE_CODES = ("command_failed", "parsing_failed", "measurement_failed", "invalid_result")
ALLOWED_CODES = {
    S.PARTIAL: FAILURE_CODES + ("command_timeout", "command_unavailable", "dependency_unavailable"),
    S.FAILED: FAILURE_CODES,
    S.TIMEOUT: ("command_timeout",),
    S.UNAVAILABLE: ("command_unavailable", "dependency_unavailable"),
    S.UNSUPPORTED: ("unsupported_platform",),
    S.NOT_COLLECTED: ("not_requested",),
}


class ZeroOffsetZone(tzinfo):
    """A zero-offset zone that is not datetime.timezone.utc."""

    def utcoffset(self, moment):
        return timedelta(0)

    def dst(self, moment):
        return timedelta(0)

    def tzname(self, moment):
        return "UTC"


ZERO_OFFSET = ZeroOffsetZone()


def issue(code, field=None):
    return CollectionIssue(code, COLLECTION_ISSUE_MESSAGES[code], field)


def timing(**overrides):
    values = {"started_at": T0, "finished_at": T1, "duration_seconds": 2.0}
    values.update(overrides)
    return values


def no_timing():
    return {"started_at": None, "finished_at": None, "duration_seconds": None}


def routing_result(status=S.COMPLETED, **overrides):
    """Build a routing envelope with valid data for the given status."""
    values = {"operation_id": "routing", "source": "linux_routing", "target": None, "status": status}
    if status is S.COMPLETED:
        values.update(value=RoutingSnapshot(hostname="host"), **timing())
    elif status is S.PARTIAL:
        values.update(value=RoutingSnapshot(), issues=(issue("command_failed", "gateway"),), **timing())
    else:
        values.update(issues=(issue(ALLOWED_CODES[status][0]),))
        if status in (S.FAILED, S.TIMEOUT):
            values.update(timing())
        else:
            values.update(no_timing())
    values.update(overrides)
    return CollectionResult(**values)


def completed(operation_id, source, target, value):
    return CollectionResult(operation_id, source, target, S.COMPLETED, value, **timing())


def ping_result(target="internet", value=None):
    value = value or PingResult(target=target, execution_status=ExecutionStatus.COMPLETED)
    return completed(f"ping.{target}", "icmp_echo", target, value)


def address_result(target="example.com", value=None):
    value = value if value is not None else DnsAddressResult(hostname=target)
    return completed(f"dns.addresses:{target}", "system_resolver", target, value)


def snapshot_values(**overrides):
    values = {
        "schema_version": 1,
        "snapshot_id": UUID4,
        "started_at": T0,
        "finished_at": T1,
        "duration_seconds": 2.0,
        "routing": routing_result(),
        "interfaces": completed("interfaces", "network_manager", None, [NetworkInterface(device="eth0")]),
        "listening_sockets": completed("listening_sockets", "socket_table", None, [ListeningSocket()]),
        "pings": (ping_result("localhost"), ping_result("internet")),
        "dns_query": completed("dns.query", "system_resolver", "example.com", DnsQueryResult()),
        "dns_latency": completed("dns.latency", "dns_response", "example.com", DnsLatencyResult()),
        "dns_addresses": (address_result(),),
    }
    values.update(overrides)
    return values


def make_snapshot(**overrides):
    return DiagnosticSnapshot(**snapshot_values(**overrides))


# --- CollectionIssue ----------------------------------------------------------


@pytest.mark.parametrize("code", COLLECTION_ISSUE_MESSAGES)
def test_issue_accepts_every_code_with_its_controlled_message(code):
    assert issue(code).message == COLLECTION_ISSUE_MESSAGES[code]


@pytest.mark.parametrize("code", ["unknown", "", None, 5, ["command_failed"]])
def test_issue_rejects_unknown_or_non_string_code(code):
    with pytest.raises(ValueError):
        CollectionIssue(code, "Diagnostic command failed.")


@pytest.mark.parametrize("message", ["Other text.", "", "Diagnostic command failed. ", None, 5])
def test_issue_rejects_message_that_differs_from_its_code(message):
    with pytest.raises(ValueError):
        CollectionIssue("command_failed", message)


def test_issue_rejects_message_of_another_code():
    with pytest.raises(ValueError):
        CollectionIssue("command_failed", COLLECTION_ISSUE_MESSAGES["command_timeout"])


@pytest.mark.parametrize("field", ["hostname", "interface", "ip_address", "gateway", "routes", None])
def test_issue_accepts_routing_fields_and_none(field):
    assert issue("command_failed", field).field == field


@pytest.mark.parametrize("field", ["", "errors", "Hostname", 5, ["gateway"]])
def test_issue_rejects_other_fields(field):
    with pytest.raises(ValueError):
        CollectionIssue("command_failed", COLLECTION_ISSUE_MESSAGES["command_failed"], field)


def test_issue_validate_catches_mutation():
    item = issue("command_failed")
    item.message = "changed"
    with pytest.raises(ValueError):
        item.validate()


# --- CollectionResult status rules -------------------------------------------


@pytest.mark.parametrize("status", list(S))
def test_result_accepts_valid_combination_for_every_status(status):
    result = routing_result(status)

    assert result.status is status


def test_completed_accepts_falsy_values():
    for value in ([], 0, False, 0.0, ""):
        result = CollectionResult("interfaces", "network_manager", None, S.COMPLETED, value, **timing())
        assert result.value == value


@pytest.mark.parametrize("status", [S.COMPLETED, S.PARTIAL])
def test_successful_statuses_require_a_value(status):
    with pytest.raises(ValueError):
        routing_result(status, value=None)


@pytest.mark.parametrize("status", [S.FAILED, S.TIMEOUT, S.UNAVAILABLE, S.UNSUPPORTED, S.NOT_COLLECTED])
def test_unsuccessful_statuses_reject_a_value(status):
    with pytest.raises(ValueError):
        routing_result(status, value=RoutingSnapshot())


def test_completed_rejects_issues():
    with pytest.raises(ValueError):
        routing_result(S.COMPLETED, issues=(issue("command_failed"),))


def test_partial_requires_issues():
    with pytest.raises(ValueError):
        routing_result(S.PARTIAL, issues=())


def test_partial_requires_a_field_on_every_issue():
    with pytest.raises(ValueError):
        routing_result(S.PARTIAL, issues=(issue("command_failed", "gateway"), issue("parsing_failed")))


@pytest.mark.parametrize("status", [S.FAILED, S.TIMEOUT, S.UNAVAILABLE, S.UNSUPPORTED, S.NOT_COLLECTED])
def test_unsuccessful_statuses_require_issues(status):
    with pytest.raises(ValueError):
        routing_result(status, issues=())


def code_cases():
    for status, allowed in ALLOWED_CODES.items():
        for code in COLLECTION_ISSUE_MESSAGES:
            yield pytest.param(status, code, code in allowed, id=f"{status.value}-{code}")


@pytest.mark.parametrize(("status", "code", "valid"), code_cases())
def test_issue_code_must_match_status(status, code, valid):
    field = "gateway" if status is S.PARTIAL else None
    if valid:
        assert routing_result(status, issues=(issue(code, field),)).issues[0].code == code
    else:
        with pytest.raises(ValueError):
            routing_result(status, issues=(issue(code, field),))


def test_one_wrong_code_among_valid_issues_is_rejected():
    with pytest.raises(ValueError):
        routing_result(S.FAILED, issues=(issue("command_failed"), issue("not_requested")))


@pytest.mark.parametrize("status", ["completed", None, 1])
def test_status_must_be_a_collection_status(status):
    with pytest.raises(ValueError):
        CollectionResult("routing", "linux_routing", None, status, RoutingSnapshot(), **timing())


@pytest.mark.parametrize("issues", [[issue("command_failed")], ("command_failed",), (None,), "x", None])
def test_issues_must_be_a_tuple_of_issue_objects(issues):
    with pytest.raises(ValueError):
        routing_result(S.FAILED, issues=issues)


def test_result_validate_rechecks_issue_objects():
    result = routing_result(S.FAILED)
    result.issues[0].message = "changed"
    with pytest.raises(ValueError):
        result.validate()


# --- Issue fields -------------------------------------------------------------


def test_field_is_rejected_on_non_routing_operation():
    with pytest.raises(ValueError):
        CollectionResult(
            "interfaces", "network_manager", None, S.FAILED,
            issues=(issue("command_failed", "gateway"),), **timing(),
        )


def test_field_is_accepted_on_failed_routing_operation():
    result = routing_result(S.FAILED, issues=(issue("command_failed", "routes"),))

    assert result.issues[0].field == "routes"


def test_partial_is_rejected_for_non_routing_operation():
    with pytest.raises(ValueError):
        CollectionResult(
            "interfaces", "network_manager", None, S.PARTIAL, [NetworkInterface()],
            (issue("command_failed", "gateway"),), **timing(),
        )


# --- operation_id, source and target -----------------------------------------

VALID_OPERATIONS = [
    ("routing", "linux_routing", None),
    ("interfaces", "network_manager", None),
    ("listening_sockets", "socket_table", None),
    ("ping.localhost", "icmp_echo", "localhost"),
    ("ping.gateway", "icmp_echo", "gateway"),
    ("ping.internet", "icmp_echo", "internet"),
    ("ping.google.com", "icmp_echo", "google.com"),
    ("dns.query", "system_resolver", "example.com"),
    ("dns.latency", "dns_response", "example.com"),
    ("dns.addresses:example.com", "system_resolver", "example.com"),
    ("dns.query", "system_resolver", "2001:db8::1"),
    ("dns.query", "system_resolver", "a" * 254),
    ("dns.addresses:" + "a" * 254, "system_resolver", "a" * 254),
]

INVALID_OPERATIONS = [
    ("unknown", "linux_routing", None),
    ("", "linux_routing", None),
    (None, "linux_routing", None),
    (5, "linux_routing", None),
    ("ROUTING", "linux_routing", None),
    ("routing", "network_manager", None),
    ("routing", None, None),
    ("routing", 5, None),
    ("routing", "linux_routing", "example.com"),
    ("interfaces", "linux_routing", None),
    ("interfaces", "network_manager", "gateway"),
    ("listening_sockets", "socket_table", ""),
    ("ping.example.com", "icmp_echo", "example.com"),
    ("ping.", "icmp_echo", ""),
    ("ping.gateway", "icmp_echo", "internet"),
    ("ping.gateway", "icmp_echo", None),
    ("ping.gateway", "system_resolver", "gateway"),
    ("ping", "icmp_echo", "gateway"),
    ("dns.query", "dns_response", "example.com"),
    ("dns.query", "system_resolver", None),
    ("dns.query", "system_resolver", 5),
    ("dns.query", "system_resolver", ""),
    ("dns.query", "system_resolver", "-example.com"),
    ("dns.query", "system_resolver", "bad host"),
    ("dns.query", "system_resolver", "example.com\n"),
    ("dns.query", "system_resolver", "exämple.com"),
    ("dns.query", "system_resolver", "a" * 255),
    ("dns.query", "system_resolver", "a/b"),
    ("dns.latency", "system_resolver", "example.com"),
    ("dns.latency", "dns_response", "--help"),
    ("dns.addresses:example.com", "dns_response", "example.com"),
    ("dns.addresses:example.com", "system_resolver", "example.org"),
    ("dns.addresses:", "system_resolver", ""),
    ("dns.addresses", "system_resolver", "example.com"),
    ("dns.addresses:-a", "system_resolver", "-a"),
    ("dns.addresses:" + "a" * 255, "system_resolver", "a" * 255),
    ("dns.other", "system_resolver", "example.com"),
]


@pytest.mark.parametrize(("operation_id", "source", "target"), VALID_OPERATIONS)
def test_result_accepts_valid_operation_source_and_target(operation_id, source, target):
    result = CollectionResult(operation_id, source, target, S.COMPLETED, object(), **timing())

    assert (result.operation_id, result.source, result.target) == (operation_id, source, target)


@pytest.mark.parametrize(("operation_id", "source", "target"), INVALID_OPERATIONS)
def test_result_rejects_invalid_operation_source_or_target(operation_id, source, target):
    with pytest.raises(ValueError):
        CollectionResult(operation_id, source, target, S.COMPLETED, object(), **timing())


class AlwaysEqualStr(str):
    """A str subclass that claims to equal and hash like anything."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0


def test_issue_rejects_str_subclass_code_message_and_field():
    message = COLLECTION_ISSUE_MESSAGES["command_failed"]
    with pytest.raises(ValueError):
        CollectionIssue(AlwaysEqualStr("command_failed"), message)
    with pytest.raises(ValueError):
        CollectionIssue("command_failed", AlwaysEqualStr(message))
    with pytest.raises(ValueError):
        CollectionIssue("command_failed", message, AlwaysEqualStr("gateway"))


@pytest.mark.parametrize(
    "build",
    [
        lambda: CollectionResult(AlwaysEqualStr("routing"), "linux_routing", None, S.COMPLETED, 1, **timing()),
        lambda: CollectionResult("routing", AlwaysEqualStr("linux_routing"), None, S.COMPLETED, 1, **timing()),
        lambda: CollectionResult(
            "ping.internet", "icmp_echo", AlwaysEqualStr("internet"), S.COMPLETED, 1, **timing()),
        lambda: CollectionResult(
            AlwaysEqualStr("ping.internet"), "icmp_echo", "internet", S.COMPLETED, 1, **timing()),
        lambda: CollectionResult(
            "dns.query", "system_resolver", AlwaysEqualStr("example.com"), S.COMPLETED, 1, **timing()),
        lambda: CollectionResult(
            "dns.addresses:example.com", "system_resolver", AlwaysEqualStr("example.com"), S.COMPLETED, 1,
            **timing()),
    ],
)
def test_result_rejects_str_subclass_identifiers(build):
    with pytest.raises(ValueError):
        build()


DNS_OPERATIONS = [
    ("dns.query", "system_resolver", "dns.query"),
    ("dns.latency", "dns_response", "dns.latency"),
    ("dns.addresses", "system_resolver", "dns.addresses:{target}"),
]


@pytest.mark.parametrize(("name", "source", "template"), DNS_OPERATIONS, ids=[o[0] for o in DNS_OPERATIONS])
@pytest.mark.parametrize(
    "target",
    ["+short", "@1.1.1.1", "a\tb", "a\x00b", "a\r", "a\n", "-x", "a" * 255],
    ids=["plus", "at", "tab", "nul", "cr", "lf", "dash", "too-long"],
)
def test_dns_operations_reject_injection_like_targets(name, source, template, target):
    operation_id = template.format(target=target)

    with pytest.raises(ValueError):
        CollectionResult(operation_id, source, target, S.COMPLETED, object(), **timing())


@pytest.mark.parametrize("target", [["internet"], {"internet"}, {}], ids=["list", "set", "dict"])
def test_ping_operation_rejects_unhashable_target(target):
    with pytest.raises(ValueError):
        CollectionResult("ping.internet", "icmp_echo", target, S.COMPLETED, object(), **timing())


# --- Timing -------------------------------------------------------------------

REQUIRED_TIMING = [S.COMPLETED, S.PARTIAL, S.FAILED, S.TIMEOUT]


@pytest.mark.parametrize("status", REQUIRED_TIMING)
def test_attempted_statuses_require_timing(status):
    with pytest.raises(ValueError):
        routing_result(status, **no_timing())


@pytest.mark.parametrize("status", [S.UNSUPPORTED, S.NOT_COLLECTED])
def test_unperformed_statuses_reject_timing(status):
    with pytest.raises(ValueError):
        routing_result(status, **timing())


def test_unavailable_accepts_complete_timing_or_none():
    assert routing_result(S.UNAVAILABLE, **timing()).duration_seconds == 2.0
    assert routing_result(S.UNAVAILABLE, **no_timing()).duration_seconds is None


@pytest.mark.parametrize("missing", ["started_at", "finished_at", "duration_seconds"])
@pytest.mark.parametrize("status", [S.COMPLETED, S.UNAVAILABLE])
def test_partial_timing_is_rejected(status, missing):
    with pytest.raises(ValueError):
        routing_result(status, **{**timing(), missing: None})


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))),
        datetime(2026, 1, 1, tzinfo=ZERO_OFFSET),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(0), "UTC")),
        datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")),
        datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/London")),
        "2026-01-01T00:00:00+00:00",
        0,
    ],
    ids=["naive", "plus-two", "zero-offset", "named-utc-timezone", "zoneinfo-utc", "london", "str", "int"],
)
def test_result_rejects_non_utc_timestamps(field, bad):
    with pytest.raises(ValueError):
        routing_result(**{field: bad})


def test_result_accepts_timezone_utc():
    assert routing_result(started_at=datetime(2026, 1, 1, tzinfo=timezone.utc)).started_at.tzinfo is timezone.utc


def test_result_allows_reversed_timestamps():
    result = routing_result(started_at=T1, finished_at=T0)

    assert result.finished_at < result.started_at


@pytest.mark.parametrize("duration", [0, 0.0, 1, 2.5, 10**6, 1e300])
def test_duration_accepts_finite_non_negative_numbers(duration):
    assert routing_result(duration_seconds=duration).duration_seconds == duration


@pytest.mark.parametrize(
    "duration",
    [math.nan, math.inf, -math.inf, -1, -0.001, True, False, "1", None, [1], 10**400, -(10**400)],
    ids=["nan", "inf", "-inf", "-1", "-0.001", "True", "False", "str", "None", "list", "huge", "-huge"],
)
def test_duration_rejects_invalid_values(duration):
    with pytest.raises(ValueError):
        routing_result(duration_seconds=duration)


# --- DiagnosticSnapshot -------------------------------------------------------


def test_snapshot_accepts_valid_sections_and_preserves_order():
    pings = (ping_result("google.com"), ping_result("localhost"), ping_result("gateway"), ping_result("internet"))
    addresses = (address_result("b.example"), address_result("a.example"))

    snapshot = make_snapshot(pings=pings, dns_addresses=addresses)

    assert [r.target for r in snapshot.pings] == ["google.com", "localhost", "gateway", "internet"]
    assert [r.target for r in snapshot.dns_addresses] == ["b.example", "a.example"]


@pytest.mark.parametrize("version", [0, 2, -1, True, 1.0, "1", None])
def test_snapshot_rejects_invalid_schema_version(version):
    with pytest.raises(ValueError):
        make_snapshot(schema_version=version)


@pytest.mark.parametrize(
    "snapshot_id",
    [
        UUID4.upper(), "{" + UUID4 + "}", UUID4.replace("-", ""), "urn:uuid:" + UUID4, UUID1,
        "00000000-0000-0000-0000-000000000000", "not-a-uuid", "", None, 5, b"x",
        "3f2b8c1e-9d4a-4b6e-8f10-2a7c5d9e1b34 ", "3f2b8c1e-9d4a-4b6e-0f10-2a7c5d9e1b34",
    ],
    ids=["upper", "braces", "no-hyphens", "urn", "uuid1", "nil", "garbage", "empty", "None", "int", "bytes",
         "trailing-space", "wrong-variant"],
)
def test_snapshot_rejects_invalid_snapshot_id(snapshot_id):
    with pytest.raises(ValueError):
        make_snapshot(snapshot_id=snapshot_id)


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
@pytest.mark.parametrize(
    "bad",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))),
     datetime(2026, 1, 1, tzinfo=ZERO_OFFSET), datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")), None],
    ids=["naive", "plus-two", "zero-offset", "zoneinfo-utc", "None"],
)
def test_snapshot_rejects_non_utc_timestamps(field, bad):
    with pytest.raises(ValueError):
        make_snapshot(**{field: bad})


def test_snapshot_allows_reversed_timestamps_and_zero_duration():
    snapshot = make_snapshot(started_at=T1, finished_at=T0, duration_seconds=0)

    assert snapshot.finished_at < snapshot.started_at


@pytest.mark.parametrize(
    "duration", [math.nan, math.inf, -math.inf, -1, True, "1", None, 10**400],
    ids=["nan", "inf", "-inf", "negative", "bool", "str", "None", "huge"],
)
def test_snapshot_rejects_invalid_duration(duration):
    with pytest.raises(ValueError):
        make_snapshot(duration_seconds=duration)


SINGLE_SECTIONS = ["routing", "interfaces", "listening_sockets", "dns_query", "dns_latency"]


@pytest.mark.parametrize("section", SINGLE_SECTIONS)
@pytest.mark.parametrize("bad", [None, "x", {}, [], object()])
def test_snapshot_rejects_non_result_sections(section, bad):
    with pytest.raises(ValueError):
        make_snapshot(**{section: bad})


@pytest.mark.parametrize("section", SINGLE_SECTIONS)
def test_snapshot_rejects_section_with_wrong_operation_id(section):
    other = {
        "routing": snapshot_values()["interfaces"],
        "interfaces": snapshot_values()["routing"],
        "listening_sockets": snapshot_values()["interfaces"],
        "dns_query": snapshot_values()["dns_latency"],
        "dns_latency": snapshot_values()["dns_query"],
    }[section]

    with pytest.raises(ValueError):
        make_snapshot(**{section: other})


@pytest.mark.parametrize(
    ("section", "operation_id", "source", "target", "bad_value"),
    [
        ("routing", "routing", "linux_routing", None, ["x"]),
        ("interfaces", "interfaces", "network_manager", None, ("eth0",)),
        ("interfaces", "interfaces", "network_manager", None, [ListeningSocket()]),
        ("interfaces", "interfaces", "network_manager", None, [NetworkInterface(), "x"]),
        ("listening_sockets", "listening_sockets", "socket_table", None, (ListeningSocket(),)),
        ("listening_sockets", "listening_sockets", "socket_table", None, [NetworkInterface()]),
        ("dns_query", "dns.query", "system_resolver", "example.com", DnsLatencyResult()),
        ("dns_latency", "dns.latency", "dns_response", "example.com", DnsQueryResult()),
    ],
)
def test_snapshot_rejects_wrong_value_types(section, operation_id, source, target, bad_value):
    result = CollectionResult(operation_id, source, target, S.COMPLETED, bad_value, **timing())

    with pytest.raises(ValueError):
        make_snapshot(**{section: result})


@pytest.mark.parametrize("group", ["pings", "dns_addresses"])
def test_snapshot_rejects_list_instead_of_tuple(group):
    results = snapshot_values()[group]

    with pytest.raises(ValueError):
        make_snapshot(**{group: list(results)})


@pytest.mark.parametrize("group", ["pings", "dns_addresses"])
@pytest.mark.parametrize("bad", [None, "x", ("x",), (None,)])
def test_snapshot_rejects_invalid_group_content(group, bad):
    with pytest.raises(ValueError):
        make_snapshot(**{group: bad})


def test_snapshot_rejects_more_than_four_pings():
    targets = ("localhost", "gateway", "internet", "google.com")
    with pytest.raises(ValueError):
        make_snapshot(pings=tuple(ping_result(t) for t in targets) + (ping_result("internet"),))


def test_snapshot_accepts_exactly_four_address_results_and_rejects_five():
    names = [f"h{i}.example" for i in range(5)]
    assert len(make_snapshot(dns_addresses=tuple(address_result(n) for n in names[:4])).dns_addresses) == 4
    with pytest.raises(ValueError):
        make_snapshot(dns_addresses=tuple(address_result(n) for n in names))


def test_snapshot_rejects_duplicate_ping_targets():
    with pytest.raises(ValueError):
        make_snapshot(pings=(ping_result("internet"), ping_result("internet")))


def test_snapshot_rejects_duplicate_address_targets():
    with pytest.raises(ValueError):
        make_snapshot(dns_addresses=(address_result("a.example"), address_result("a.example")))


def test_snapshot_rejects_wrong_ping_and_address_value_types():
    wrong_ping = completed("ping.internet", "icmp_echo", "internet", DnsQueryResult())
    wrong_address = completed("dns.addresses:a.example", "system_resolver", "a.example", PingResult())
    with pytest.raises(ValueError):
        make_snapshot(pings=(wrong_ping,))
    with pytest.raises(ValueError):
        make_snapshot(dns_addresses=(wrong_address,))


def test_snapshot_rejects_address_group_holding_ping_result():
    with pytest.raises(ValueError):
        make_snapshot(dns_addresses=(ping_result("internet"),))


def test_snapshot_accepts_empty_groups_and_empty_lists():
    snapshot = make_snapshot(
        pings=(), dns_addresses=(),
        interfaces=completed("interfaces", "network_manager", None, []),
        listening_sockets=completed("listening_sockets", "socket_table", None, []),
    )

    assert snapshot.pings == () and snapshot.dns_addresses == ()
    assert snapshot.interfaces.value == [] and snapshot.listening_sockets.value == []


def test_snapshot_accepts_non_completed_sections():
    unsupported = CollectionResult(
        "ping.internet", "icmp_echo", "internet", S.UNSUPPORTED, issues=(issue("unsupported_platform"),),
    )
    snapshot = make_snapshot(routing=routing_result(S.TIMEOUT), pings=(unsupported,))

    assert snapshot.pings[0].value is None


def test_snapshot_accepts_valid_zero_and_empty_measurements():
    zero_ping = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED, packets_sent=4, packets_received=4,
        packet_loss_percent=0.0, min_rtt_ms=0.0, avg_rtt_ms=0.0, max_rtt_ms=0.0, mdev_ms=0.0, reachable=True,
    )
    snapshot = make_snapshot(pings=(ping_result("internet", zero_ping),))

    assert snapshot.pings[0].value.packet_loss_percent == 0.0
    assert snapshot.pings[0].value.avg_rtt_ms == 0.0


def test_snapshot_accepts_full_packet_loss_with_no_rtt():
    lost = PingResult(
        target="gateway", execution_status=ExecutionStatus.COMPLETED, packets_sent=3, packets_received=0,
        packet_loss_percent=100.0, reachable=False, packet_loss_status=PacketLossStatus.TOTAL_LOSS,
    )

    snapshot = make_snapshot(pings=(ping_result("gateway", lost),))

    assert snapshot.pings[0].value.min_rtt_ms is None


def test_snapshot_accepts_nxdomain_and_none_values_as_completed_data():
    nxdomain = DnsLatencyResult(
        hostname="missing.example", execution_status=ExecutionStatus.COMPLETED, dns_status="NXDOMAIN",
        resolved=False, dns_latency_ms=None, answer_count=0,
    )
    routing = RoutingSnapshot(hostname="host", gateway=None, routes=[])

    snapshot = make_snapshot(
        dns_latency=completed("dns.latency", "dns_response", "missing.example", nxdomain),
        routing=routing_result(value=routing),
    )

    assert snapshot.dns_latency.value.resolved is False
    assert snapshot.routing.value.gateway is None


def test_snapshot_rejects_invalid_nested_result_state():
    result = routing_result()
    result.value = None

    with pytest.raises(ValueError):
        make_snapshot(routing=result)


# --- Ownership and mutation ---------------------------------------------------


def test_snapshot_does_not_copy_values_and_shows_later_mutation():
    interfaces = [NetworkInterface(device="eth0")]
    sockets = [ListeningSocket()]
    pings = (ping_result("internet"),)
    snapshot = make_snapshot(
        interfaces=completed("interfaces", "network_manager", None, interfaces),
        listening_sockets=completed("listening_sockets", "socket_table", None, sockets),
        pings=pings,
    )

    interfaces.append(NetworkInterface(device="wlan0"))

    assert snapshot.interfaces.value is interfaces
    assert snapshot.listening_sockets.value is sockets
    assert snapshot.pings is pings
    assert [item.device for item in snapshot.interfaces.value] == ["eth0", "wlan0"]


def test_validate_passes_on_valid_snapshot_and_can_be_repeated():
    snapshot = make_snapshot()

    snapshot.validate()
    snapshot.validate()


@pytest.mark.parametrize(
    ("attribute", "bad"),
    [
        ("schema_version", 2),
        ("snapshot_id", "x"),
        ("duration_seconds", math.nan),
        ("started_at", datetime(2026, 1, 1)),
        ("pings", [ping_result("internet")]),
        ("dns_addresses", None),
        ("routing", None),
    ],
)
def test_validate_catches_invalid_snapshot_mutation(attribute, bad):
    snapshot = make_snapshot()
    setattr(snapshot, attribute, bad)

    with pytest.raises(ValueError):
        snapshot.validate()


def test_validate_catches_value_list_mutation_to_wrong_element_type():
    interfaces = [NetworkInterface()]
    snapshot = make_snapshot(interfaces=completed("interfaces", "network_manager", None, interfaces))
    interfaces.append("not an interface")

    with pytest.raises(ValueError):
        snapshot.validate()


def test_validate_catches_nested_result_mutation():
    snapshot = make_snapshot()
    snapshot.routing.status = S.FAILED

    with pytest.raises(ValueError):
        snapshot.validate()


def test_result_validate_catches_mutation():
    result = routing_result()
    result.duration_seconds = -1

    with pytest.raises(ValueError):
        result.validate()


@pytest.mark.parametrize(
    "build",
    [
        lambda: CollectionResult("routing", "linux_routing", None, S.FAILED, issues=(issue("command_failed"),),
                                 started_at=object(), finished_at=T1, duration_seconds=1),
        lambda: CollectionResult(["routing"], "linux_routing", None, S.COMPLETED, RoutingSnapshot(), **timing()),
        lambda: CollectionResult("routing", ["x"], None, S.COMPLETED, RoutingSnapshot(), **timing()),
        lambda: CollectionResult("routing", "linux_routing", None, S.COMPLETED, RoutingSnapshot(),
                                 started_at=T0, finished_at=T1, duration_seconds=object()),
        lambda: make_snapshot(snapshot_id=["x"]),
        lambda: make_snapshot(started_at=object()),
    ],
)
def test_invalid_types_raise_value_error_not_type_error(build):
    with pytest.raises(ValueError):
        build()
