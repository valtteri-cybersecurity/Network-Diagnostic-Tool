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
    value = value if value is not None else valid_value("dns.addresses", target)
    return completed(f"dns.addresses:{target}", "system_resolver", target, value)


COMPLETE = ExecutionStatus.COMPLETED


def dns_query_value(target="example.com", **fields):
    return DnsQueryResult(hostname=target, execution_status=COMPLETE, **fields)


def dns_latency_value(target="example.com", **fields):
    return DnsLatencyResult(hostname=target, execution_status=COMPLETE, **fields)


def valid_value(operation_id, target):
    """Return a valid observation for any operation id and target."""
    if operation_id == "routing":
        return RoutingSnapshot(hostname="host")
    if operation_id in ("interfaces", "listening_sockets"):
        return []
    if operation_id.startswith("ping."):
        return PingResult(target=target, execution_status=COMPLETE)
    if operation_id == "dns.query":
        return dns_query_value(target)
    if operation_id == "dns.latency":
        return dns_latency_value(target)
    return DnsAddressResult(hostname=target, execution_status=COMPLETE)


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
        "dns_query": completed("dns.query", "system_resolver", "example.com", dns_query_value()),
        "dns_latency": completed("dns.latency", "dns_response", "example.com", dns_latency_value()),
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
    zero_ping = PingResult(
        target="internet", execution_status=COMPLETE, packets_sent=0, packets_received=0,
        packet_loss_percent=0.0, avg_rtt_ms=0.0, reachable=False, icmp_errors=0,
    )
    empty_routing = RoutingSnapshot(hostname="", routes=[])
    cases = [
        ("interfaces", "network_manager", None, []),
        ("listening_sockets", "socket_table", None, []),
        ("routing", "linux_routing", None, empty_routing),
        ("ping.internet", "icmp_echo", "internet", zero_ping),
        ("dns.latency", "dns_response", "example.com",
         dns_latency_value(resolved=False, dns_latency_ms=0.0, answer_count=0)),
        ("dns.addresses:example.com", "system_resolver", "example.com",
         DnsAddressResult(hostname="example.com", execution_status=COMPLETE)),
    ]
    for operation_id, source, target, value in cases:
        result = CollectionResult(operation_id, source, target, S.COMPLETED, value, **timing())
        assert result.value is value


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
    return [
        pytest.param(status, code, code in allowed, id=f"{status.value}-{code}")
        for status, allowed in ALLOWED_CODES.items()
        for code in COLLECTION_ISSUE_MESSAGES
    ]


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
    value = valid_value(operation_id, target)
    result = CollectionResult(operation_id, source, target, S.COMPLETED, value, **timing())

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
        ("dns_query", "dns.query", "system_resolver", "example.com", dns_latency_value()),
        ("dns_latency", "dns.latency", "dns_response", "example.com", dns_query_value()),
    ],
)
def test_snapshot_rejects_wrong_value_types(section, operation_id, source, target, bad_value):
    with pytest.raises(ValueError):
        result = CollectionResult(operation_id, source, target, S.COMPLETED, bad_value, **timing())
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
    with pytest.raises(ValueError):
        wrong_ping = completed("ping.internet", "icmp_echo", "internet", dns_query_value("internet"))
        make_snapshot(pings=(wrong_ping,))
    with pytest.raises(ValueError):
        wrong_address = completed(
            "dns.addresses:a.example", "system_resolver", "a.example", valid_value("ping.internet", "a.example"),
        )
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


# --- Nested value validation (H2, H3, H4, D3) ---------------------------------

NAN = math.nan
INF = math.inf
BAD_MEASUREMENTS = [NAN, INF, -INF, -0.1, -1, 10**400, True, "1.0", [1.0]]
CONTROL_CHARACTERS = ["\x00", "\x1b", "\x7f", "\x85"]


def ping_with(**fields):
    value = PingResult(target="internet", execution_status=COMPLETE, **fields)
    return completed("ping.internet", "icmp_echo", "internet", value)


def latency_with(**fields):
    return completed("dns.latency", "dns_response", "example.com", dns_latency_value(**fields))


def query_with(**fields):
    return completed("dns.query", "system_resolver", "example.com", dns_query_value(**fields))


def addresses_with(**fields):
    value = DnsAddressResult(hostname="example.com", execution_status=COMPLETE, **fields)
    return completed("dns.addresses:example.com", "system_resolver", "example.com", value)


def interfaces_with(**fields):
    return completed("interfaces", "network_manager", None, [NetworkInterface(**fields)])


def sockets_with(**fields):
    return completed("listening_sockets", "socket_table", None, [ListeningSocket(**fields)])


def routing_with(**fields):
    return completed("routing", "linux_routing", None, RoutingSnapshot(**fields))


def accepted(build, **fields):
    assert build(**fields).value is not None


def rejected(build, **fields):
    with pytest.raises(ValueError):
        build(**fields)


class SubPing(PingResult):
    pass


class SubSocket(ListeningSocket):
    pass


@pytest.mark.parametrize(
    ("operation_id", "source", "target", "wrong"),
    [
        ("routing", "linux_routing", None, NetworkInterface()),
        ("interfaces", "network_manager", None, [ListeningSocket()]),
        ("listening_sockets", "socket_table", None, [NetworkInterface()]),
        ("ping.internet", "icmp_echo", "internet", dns_query_value("internet")),
        ("ping.internet", "icmp_echo", "internet", SubPing(target="internet", execution_status=COMPLETE)),
        ("dns.query", "system_resolver", "example.com", dns_latency_value()),
        ("dns.latency", "dns_response", "example.com", dns_query_value()),
        ("dns.addresses:example.com", "system_resolver", "example.com", dns_query_value()),
        ("listening_sockets", "socket_table", None, [SubSocket()]),
        ("interfaces", "network_manager", None, (NetworkInterface(),)),
    ],
)
def test_result_rejects_wrong_nested_value_type_at_construction(operation_id, source, target, wrong):
    with pytest.raises(ValueError):
        CollectionResult(operation_id, source, target, S.COMPLETED, wrong, **timing())


@pytest.mark.parametrize("execution_status", [ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT, "completed", None])
@pytest.mark.parametrize("operation_id", ["ping.internet", "dns.query", "dns.latency", "dns.addresses:internet"])
def test_nested_value_must_have_completed_execution_status(operation_id, execution_status):
    target = "internet"
    value = valid_value(operation_id, target)
    value.execution_status = execution_status
    source = "icmp_echo" if operation_id.startswith("ping") else (
        "dns_response" if operation_id == "dns.latency" else "system_resolver")

    with pytest.raises(ValueError):
        CollectionResult(operation_id, source, target, S.COMPLETED, value, **timing())


@pytest.mark.parametrize("alias", ["localhost", "gateway", "internet", "google.com"])
def test_ping_value_target_must_equal_the_alias_not_an_address(alias):
    ok = PingResult(target=alias, execution_status=COMPLETE)
    assert CollectionResult(f"ping.{alias}", "icmp_echo", alias, S.COMPLETED, ok, **timing()).value is ok
    for wrong in ("8.8.8.8", "127.0.0.1", "", "internet" if alias != "internet" else "gateway", None, 5):
        bad = PingResult(target=wrong, execution_status=COMPLETE)
        with pytest.raises(ValueError):
            CollectionResult(f"ping.{alias}", "icmp_echo", alias, S.COMPLETED, bad, **timing())


@pytest.mark.parametrize("build", [query_with, latency_with, addresses_with], ids=["query", "latency", "addresses"])
@pytest.mark.parametrize("hostname", ["example.org", "", "EXAMPLE.COM", "example.com.", None, 5])
def test_dns_value_hostname_must_match_target(build, hostname):
    result = build()
    result.value.hostname = hostname

    with pytest.raises(ValueError):
        result.validate()


@pytest.mark.parametrize("field", ["min_rtt_ms", "avg_rtt_ms", "max_rtt_ms", "mdev_ms", "packet_loss_percent"])
@pytest.mark.parametrize("bad", BAD_MEASUREMENTS, ids=repr)
def test_ping_measurements_reject_non_finite_negative_and_wrong_types(field, bad):
    rejected(ping_with, **{field: bad})


@pytest.mark.parametrize("field", ["min_rtt_ms", "avg_rtt_ms", "max_rtt_ms", "mdev_ms"])
@pytest.mark.parametrize("good", [None, 0, 0.0, 1, 12.5, 1e308])
def test_ping_rtt_accepts_none_zero_and_positive_numbers(field, good):
    accepted(ping_with, **{field: good})


@pytest.mark.parametrize("bad", BAD_MEASUREMENTS, ids=repr)
def test_dns_latency_rejects_non_finite_negative_and_wrong_types(bad):
    rejected(latency_with, dns_latency_ms=bad)


@pytest.mark.parametrize("good", [None, 0, 0.0, 0.001, 250, 1e308])
def test_dns_latency_accepts_none_zero_and_positive_numbers(good):
    accepted(latency_with, dns_latency_ms=good)


@pytest.mark.parametrize("good", [None, 0, 0.0, 99.9, 100, 100.0])
def test_packet_loss_accepts_both_ends_of_range(good):
    accepted(ping_with, packet_loss_percent=good)


@pytest.mark.parametrize("bad", [-0.0001, -1, math.nextafter(100.0, 101.0), 100.1, 101, 1000])
def test_packet_loss_rejects_values_outside_zero_to_hundred(bad):
    rejected(ping_with, packet_loss_percent=bad)


@pytest.mark.parametrize(
    ("sent", "received", "valid"),
    [(4, 4, True), (4, 3, True), (4, 0, True), (0, 0, True), (4, 5, False), (0, 1, False),
     (None, 5, True), (4, None, True), (None, None, True)],
)
def test_ping_received_must_not_exceed_sent(sent, received, valid):
    check = accepted if valid else rejected
    check(ping_with, packets_sent=sent, packets_received=received)


@pytest.mark.parametrize("field", ["packets_sent", "packets_received", "icmp_errors"])
@pytest.mark.parametrize("bad", [-1, True, False, 1.0, "1", 2**64 + 0.5])
def test_ping_counts_reject_negative_bool_float_and_text(field, bad):
    rejected(ping_with, **{field: bad})


@pytest.mark.parametrize("field", ["packets_sent", "icmp_errors"])
@pytest.mark.parametrize("good", [None, 0, 1, 2**40])
def test_ping_counts_accept_none_zero_and_large_integers(field, good):
    accepted(ping_with, **{field: good})


@pytest.mark.parametrize(("count", "valid"), [(None, True), (0, True), (1, True), (65535, True),
                                               (65536, False), (-1, False), (2**31, False)])
def test_dns_answer_count_is_limited_to_sixteen_bits(count, valid):
    check = accepted if valid else rejected
    check(latency_with, answer_count=count)


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", [1]])
def test_dns_answer_count_rejects_bool_float_and_other_types(bad):
    rejected(latency_with, answer_count=bad)


@pytest.mark.parametrize(
    ("build", "field"),
    [(ping_with, "reachable"), (query_with, "resolved"), (latency_with, "resolved")],
)
@pytest.mark.parametrize(("value", "valid"), [(None, True), (True, True), (False, True),
                                               (1, False), (0, False), ("true", False)])
def test_flags_accept_only_bool_or_none(build, field, value, valid):
    check = accepted if valid else rejected
    check(build, **{field: value})


def test_bool_is_not_accepted_as_int_or_float_but_int_is_accepted_as_float():
    rejected(ping_with, packets_sent=True)
    rejected(ping_with, avg_rtt_ms=True)
    rejected(latency_with, dns_latency_ms=False)
    accepted(ping_with, avg_rtt_ms=5)
    rejected(ping_with, packets_sent=4.0)


@pytest.mark.parametrize("status", ["completed", "no_loss", None, 1])
def test_ping_packet_loss_status_must_be_enum(status):
    rejected(ping_with, packet_loss_status=status)


@pytest.mark.parametrize("status", list(PacketLossStatus))
def test_ping_packet_loss_status_accepts_every_enum_member(status):
    accepted(ping_with, packet_loss_status=status)


@pytest.mark.parametrize(
    "legacy",
    [{"error": 5}, {"raw_output": 5}, {"latency_analysis": 5}, {"rtt_range_analysis": b"x"}],
)
def test_ping_legacy_text_fields_must_be_strings_or_none(legacy):
    rejected(ping_with, **legacy)


def test_ping_legacy_text_is_not_restricted_to_printable_characters():
    accepted(ping_with, error="bad \x1b[31m output", raw_output="line\n\x00", latency_analysis="x\x7f")


@pytest.mark.parametrize("build", [query_with, latency_with, addresses_with])
def test_dns_error_must_be_string_or_none(build):
    result = build()
    result.value.error = 5
    with pytest.raises(ValueError):
        result.validate()
    result.value.error = "raw \x1b error"
    result.validate()


def test_dns_latency_analysis_must_be_latency_analysis_or_none():
    from network_diagnostic.core.models import LatencyAnalysis

    accepted(latency_with, latency_analysis=LatencyAnalysis(latency_ms=3.0, status="good"))
    rejected(latency_with, latency_analysis="good")


@pytest.mark.parametrize("status", ["NOERROR", "NXDOMAIN", "SERVFAIL", "A", "A" * 32, "REFUSED1", None])
def test_dns_status_accepts_response_codes(status):
    accepted(latency_with, dns_status=status)


@pytest.mark.parametrize("status", ["noerror", "", "A" * 33, "1ERROR", "NO ERROR", "NO-ERROR", 5, "NXDOMAIN\n"])
def test_dns_status_rejects_other_text(status):
    rejected(latency_with, dns_status=status)


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
def test_dns_status_rejects_control_characters(char):
    rejected(latency_with, dns_status="NO" + char + "ERROR")


@pytest.mark.parametrize("good", [None, "93.184.216.34", "2001:db8::1", "::1", "0.0.0.0"])
def test_dns_query_ip_address_accepts_valid_syntax(good):
    accepted(query_with, ip_address=good)


@pytest.mark.parametrize(
    "bad",
    ["999.1.1.1", "1.2.3", "example.com", "", " 1.1.1.1", "1.1.1.1\n", "2001:db8:::1", 5, "1.1.1.1\x00", "01.1.1.1"],
)
def test_dns_query_ip_address_rejects_invalid_syntax(bad):
    rejected(query_with, ip_address=bad)


@pytest.mark.parametrize(
    ("fields", "valid"),
    [
        ({}, True),
        ({"ipv4_addresses": ["93.184.216.34", "1.1.1.1"], "ipv6_addresses": ["2001:db8::1"]}, True),
        ({"ipv4_addresses": ["2001:db8::1"]}, False),
        ({"ipv6_addresses": ["93.184.216.34"]}, False),
        ({"ipv4_addresses": [None]}, False),
        ({"ipv6_addresses": [None]}, False),
        ({"ipv4_addresses": ("1.1.1.1",)}, False),
        ({"ipv6_addresses": "2001:db8::1"}, False),
        ({"ipv4_addresses": ["999.1.1.1"]}, False),
        ({"ipv6_addresses": ["2001:db8::g"]}, False),
        ({"ipv4_addresses": [5]}, False),
        ({"ipv4_addresses": ["1.1.1.1\x00"]}, False),
    ],
)
def test_dns_address_lists_validate_version_and_syntax(fields, valid):
    check = accepted if valid else rejected
    check(addresses_with, **fields)


@pytest.mark.parametrize("field", ["port", "peer"])
@pytest.mark.parametrize("good", [None, "*", "0", "1", "22", "65535", "00080"])
def test_socket_ports_accept_star_and_range(field, good):
    key = "local_port" if field == "port" else "peer_port"
    accepted(sockets_with, **{key: good})


@pytest.mark.parametrize("field", ["local_port", "peer_port"])
@pytest.mark.parametrize(
    "bad", ["65536", "99999", "100000", "-1", "", " 80", "80 ", "8 0", "http", "**", "٣٣", 80, 0, "1e3", "0x50", "80\x00"],
)
def test_socket_ports_reject_out_of_range_and_other_text(field, bad):
    rejected(sockets_with, **{field: bad})


@pytest.mark.parametrize("field", ["local_address", "peer_address"])
@pytest.mark.parametrize(
    "good", [None, "*", "0.0.0.0", "127.0.0.1", "::", "::1", "[::1]", "[::]", "[fe80::1%eth0]", "[fe80::1]%eth0",
             "fe80::1%eth0", "127.0.0.1%lo", "2001:db8::1"],
)
def test_socket_addresses_accept_ss_forms(field, good):
    accepted(sockets_with, **{field: good})


@pytest.mark.parametrize("field", ["local_address", "peer_address"])
@pytest.mark.parametrize(
    "bad",
    ["", "a b", "999.1.1.1", "example.com", "[::1", "[::1]x", "[::1%]", "[fe80::1%eth0]%eth1", "[fe80::1]%",
     "[fe80::1]%bad iface", "%eth0", "::1%-bad", "::g", "1.2.3", "[1.2.3.4", 5, "127.0.0.1\x00", "\x1b[31m*"],
)
def test_socket_addresses_reject_invalid_forms(field, bad):
    rejected(sockets_with, **{field: bad})


@pytest.mark.parametrize("field", ["protocol", "state"])
@pytest.mark.parametrize("good", [None, "tcp", "udp", "LISTEN", "UNCONN"])
def test_socket_tokens_accept_single_words(field, good):
    accepted(sockets_with, **{field: good})


@pytest.mark.parametrize("field", ["protocol", "state"])
@pytest.mark.parametrize("bad", ["", "tcp udp", " tcp", "tcp ", "tc\tp", 5])
def test_socket_tokens_reject_empty_whitespace_and_non_text(field, bad):
    rejected(sockets_with, **{field: bad})


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
@pytest.mark.parametrize("field", ["protocol", "state", "local_address", "peer_address", "local_port", "peer_port"])
def test_socket_fields_reject_control_characters(field, char):
    rejected(sockets_with, **{field: "1" + char})


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
@pytest.mark.parametrize("field", ["device", "interface_type", "state", "connection"])
def test_interface_fields_reject_control_characters(field, char):
    rejected(interfaces_with, **{field: "Office" + char + "WiFi"})


@pytest.mark.parametrize("char", CONTROL_CHARACTERS)
def test_routing_hostname_rejects_control_characters(char):
    rejected(routing_with, hostname="host" + char)


@pytest.mark.parametrize(
    ("char", "valid"),
    [("\x1f", False), ("\x20", True), ("\x7e", True), ("\x7f", False), ("\x9f", False), ("\xa0", True), ("é", True)],
)
def test_control_character_range_has_exact_boundaries(char, valid):
    check = accepted if valid else rejected
    check(interfaces_with, connection="a" + char + "b")


@pytest.mark.parametrize("char", ["\n", "\r", "\t", "\x0b", "\x0c"])
def test_whitespace_controls_are_rejected_in_observed_text(char):
    rejected(interfaces_with, connection="a" + char)
    rejected(routing_with, hostname="a" + char)


def test_observed_text_with_markup_or_shell_syntax_is_valid_data():
    markup = "<script>alert(1)</script> $(id) ; ' \" & \\"
    accepted(interfaces_with, connection=markup, device=markup)
    accepted(routing_with, hostname=markup)


@pytest.mark.parametrize("bad", [5, b"eth0", ["eth0"], True])
def test_observed_text_must_be_exact_str(bad):
    rejected(interfaces_with, connection=bad)
    rejected(routing_with, hostname=bad)


def test_observed_text_rejects_str_subclass():
    class Name(str):
        pass

    rejected(interfaces_with, connection=Name("x"))
    rejected(routing_with, hostname=Name("x"))


@pytest.mark.parametrize(
    "fields",
    [
        {"hostname": "host", "interface": "eth0", "ip_address": "192.168.1.5", "gateway": "192.168.1.1",
         "routes": ["default via 192.168.1.1 dev eth0"]},
        {"routes": []},
        {"interface": "a" * 15},
        {"interface": "br-lan.10"},
        {"ip_address": "0.0.0.0", "gateway": "255.255.255.255"},
    ],
)
def test_routing_value_accepts_valid_fields(fields):
    accepted(routing_with, **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"interface": "a" * 16},
        {"interface": ""},
        {"interface": "-eth0"},
        {"interface": ".."},
        {"interface": "eth 0"},
        {"interface": 5},
        {"ip_address": "2001:db8::1"},
        {"ip_address": "256.1.1.1"},
        {"gateway": "1.2.3"},
        {"gateway": "not-an-ip"},
        {"gateway": ""},
        {"ip_address": 5},
        {"routes": ("default",)},
        {"routes": "default"},
        {"routes": [5]},
        {"routes": [None]},
        {"errors": None},
        {"errors": []},
        {"errors": {"gateway": 5}},
        {"errors": {"unknown": "x"}},
        {"errors": {5: "x"}},
    ],
)
def test_routing_value_rejects_invalid_fields(fields):
    rejected(routing_with, **fields)


def test_routing_routes_are_free_text_lines_without_control_character_check():
    accepted(routing_with, routes=["default via 1.1.1.1 dev eth0\x1b", ""])


@pytest.mark.parametrize("errors", [{"gateway": "Command failed"}, {"routes": "x", "hostname": "y"}])
def test_completed_routing_rejects_non_empty_errors(errors):
    rejected(routing_with, errors=errors)


def partial_routing(issues, **fields):
    return CollectionResult(
        "routing", "linux_routing", None, S.PARTIAL, RoutingSnapshot(**fields), tuple(issues), **timing(),
    )


@pytest.mark.parametrize("field", ["hostname", "interface", "ip_address", "gateway", "routes"])
def test_partial_routing_issue_field_must_be_unavailable_in_value(field):
    available = {
        "hostname": "host", "interface": "eth0", "ip_address": "192.168.1.5",
        "gateway": "192.168.1.1", "routes": [],
    }

    assert partial_routing([issue("command_failed", field)]).value is not None
    with pytest.raises(ValueError):
        partial_routing([issue("command_failed", field)], **{field: available[field]})
    other = next(name for name in available if name != field)
    assert partial_routing([issue("command_failed", field)], **{other: available[other]}).value is not None


def test_partial_routing_checks_every_issue_field():
    issues = [issue("command_failed", "gateway"), issue("measurement_failed", "interface")]

    with pytest.raises(ValueError):
        partial_routing(issues, interface="eth0")
    assert partial_routing(issues, hostname="host").value.hostname == "host"


# --- Duplicate issues (H3) ----------------------------------------------------

ROUTING_FIELD_NAMES = ("hostname", "interface", "ip_address", "gateway", "routes")
PARTIAL_CODES = ALLOWED_CODES[S.PARTIAL]


def test_duplicate_code_and_field_issues_are_rejected():
    with pytest.raises(ValueError):
        partial_routing([issue("command_failed", "gateway"), issue("command_failed", "gateway")])
    with pytest.raises(ValueError):
        routing_result(S.FAILED, issues=(issue("command_failed"), issue("command_failed")))
    with pytest.raises(ValueError):
        CollectionResult(
            "ping.internet", "icmp_echo", "internet", S.NOT_COLLECTED,
            issues=(issue("not_requested"), issue("not_requested")),
        )


def test_distinct_issue_pairs_are_accepted():
    same_code = [issue("command_failed", "gateway"), issue("command_failed", "interface")]
    same_field = [issue("command_failed", "gateway"), issue("parsing_failed", "gateway")]
    none_and_field = routing_result(S.FAILED, issues=(issue("command_failed"), issue("command_failed", "routes")))

    assert len(partial_routing(same_code).issues) == 2
    assert len(partial_routing(same_field).issues) == 2
    assert len(none_and_field.issues) == 2
    other = CollectionResult(
        "dns.query", "system_resolver", "example.com", S.FAILED,
        issues=(issue("command_failed"), issue("parsing_failed")), **timing(),
    )
    assert len(other.issues) == 2


def test_maximum_valid_partial_routing_has_seven_codes_times_five_fields():
    issues = [issue(code, name) for code in PARTIAL_CODES for name in ROUTING_FIELD_NAMES]

    result = partial_routing(issues)

    assert len(PARTIAL_CODES) == 7 and len(result.issues) == 35
    with pytest.raises(ValueError):
        partial_routing(issues + [issue("invalid_result", "routes")])


def test_one_duplicate_among_many_issues_is_rejected():
    issues = [issue(code, name) for code in PARTIAL_CODES for name in ROUTING_FIELD_NAMES]
    issues.append(issues[17])

    with pytest.raises(ValueError):
        partial_routing(issues)


def test_validate_catches_duplicate_issue_added_after_construction():
    result = partial_routing([issue("command_failed", "gateway")])
    result.issues = result.issues + (issue("command_failed", "gateway"),)

    with pytest.raises(ValueError):
        result.validate()


# --- Snapshot-level checks of nested values -----------------------------------


@pytest.mark.parametrize(
    ("section", "mutate"),
    [
        ("pings", lambda s: setattr(s.pings[0].value, "avg_rtt_ms", NAN)),
        ("pings", lambda s: setattr(s.pings[0].value, "target", "gateway")),
        ("dns_latency", lambda s: setattr(s.dns_latency.value, "answer_count", 65536)),
        ("dns_query", lambda s: setattr(s.dns_query.value, "ip_address", "999.9.9.9")),
        ("dns_addresses", lambda s: s.dns_addresses[0].value.ipv4_addresses.append("::1")),
        ("interfaces", lambda s: setattr(s.interfaces.value[0], "connection", "x\x1b")),
        ("listening_sockets", lambda s: setattr(s.listening_sockets.value[0], "local_port", "65536")),
        ("routing", lambda s: setattr(s.routing.value, "errors", {"gateway": "late"})),
    ],
)
def test_snapshot_validate_catches_nested_value_mutation(section, mutate):
    snapshot = make_snapshot()
    snapshot.validate()
    mutate(snapshot)

    with pytest.raises(ValueError):
        snapshot.validate()


@pytest.mark.parametrize(
    ("section", "operation_id", "source", "bad_value"),
    [
        ("routing", "routing", "linux_routing", [NetworkInterface()]),
        ("interfaces", "interfaces", "network_manager", [NetworkInterface(), ListeningSocket()]),
    ],
)
def test_snapshot_section_type_checks_hold_even_if_result_validation_is_bypassed(
    monkeypatch, section, operation_id, source, bad_value,
):
    monkeypatch.setattr(CollectionResult, "validate", lambda self: None)
    result = CollectionResult(operation_id, source, None, S.COMPLETED, bad_value, **timing())

    with pytest.raises(ValueError):
        make_snapshot(**{section: result})


# --- JSON safe integers (M1), IPv6 scope (L1), deliberate D3 policy -------------------------

SAFE = 2**53 - 1


@pytest.mark.parametrize("field", ["packets_sent", "icmp_errors"])
def test_ping_counts_have_an_exact_safe_integer_bound(field):
    accepted(ping_with, **{field: SAFE})
    rejected(ping_with, **{field: SAFE + 1})


def test_ping_received_count_has_the_same_safe_integer_bound():
    accepted(ping_with, packets_sent=SAFE, packets_received=SAFE)
    rejected(ping_with, packets_sent=SAFE + 1, packets_received=SAFE + 1)


@pytest.mark.parametrize("field", ["min_rtt_ms", "avg_rtt_ms", "max_rtt_ms", "mdev_ms", "packet_loss_percent"])
def test_ping_int_measurements_have_an_exact_safe_integer_bound(field):
    # packet_loss_percent is capped at 100, so only the RTT fields reach the bound.
    if field == "packet_loss_percent":
        accepted(ping_with, **{field: 100})
        rejected(ping_with, **{field: SAFE + 1})
    else:
        accepted(ping_with, **{field: SAFE})
        rejected(ping_with, **{field: SAFE + 1})


def test_dns_latency_int_has_an_exact_safe_integer_bound_but_floats_are_not_capped():
    accepted(latency_with, dns_latency_ms=SAFE)
    rejected(latency_with, dns_latency_ms=SAFE + 1)
    accepted(latency_with, dns_latency_ms=float(SAFE + 1))
    accepted(latency_with, dns_latency_ms=1e300)


def test_result_and_snapshot_duration_have_an_exact_safe_integer_bound():
    assert routing_result(duration_seconds=SAFE).duration_seconds == SAFE
    with pytest.raises(ValueError):
        routing_result(duration_seconds=SAFE + 1)
    assert make_snapshot(duration_seconds=SAFE).duration_seconds == SAFE
    with pytest.raises(ValueError):
        make_snapshot(duration_seconds=SAFE + 1)
    assert routing_result(duration_seconds=1e300).duration_seconds == 1e300


@pytest.mark.parametrize(
    ("build", "key", "extra"),
    [(query_with, "ip_address", {}), (addresses_with, "ipv6_addresses", {"list": True})],
    ids=["query", "addresses"],
)
@pytest.mark.parametrize("scope", ["eth0", "a" * 15, "br-lan.10"])
def test_ipv6_scope_accepts_conservative_interface_names(build, key, extra, scope):
    address = f"fe80::1%{scope}"
    accepted(build, **{key: [address] if extra else address})


@pytest.mark.parametrize(
    ("build", "key", "extra"),
    [(query_with, "ip_address", {}), (addresses_with, "ipv6_addresses", {"list": True})],
    ids=["query", "addresses"],
)
@pytest.mark.parametrize("scope", ["<script>", "a" * 16, "", "eth 0", "-eth0", "..", "eth0%eth1", "e\x1b"])
def test_ipv6_scope_rejects_unsafe_or_invalid_names(build, key, extra, scope):
    address = f"fe80::1%{scope}"
    rejected(build, **{key: [address] if extra else address})


@pytest.mark.parametrize("char", ["\u202e", "\u2028", "\u200b", "\ufeff"])
def test_bidi_and_unicode_line_separators_are_deliberately_accepted_as_observed_text(char):
    # D3 policy: only C0, DEL and C1 controls are rejected. Changing this must be a conscious decision.
    text = "Wi" + char + "Fi"
    result = interfaces_with(connection=text)

    assert result.value[0].connection == text


# --- Exact types of envelope metadata -----------------------------------------------------


class IntSub(int):
    pass


class FloatSub(float):
    pass


class StrSub(str):
    pass


class DatetimeSub(datetime):
    pass


@pytest.mark.parametrize("duration", [IntSub(1), FloatSub(1.5), IntSub(0)], ids=["int", "float", "zero_int"])
def test_duration_rejects_int_and_float_subclasses(duration):
    with pytest.raises(ValueError):
        routing_result(duration_seconds=duration)
    with pytest.raises(ValueError):
        make_snapshot(duration_seconds=duration)


def test_snapshot_id_rejects_str_subclass():
    with pytest.raises(ValueError):
        make_snapshot(snapshot_id=StrSub(UUID4))


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
def test_timestamps_reject_datetime_subclass_with_utc(field):
    subclass = DatetimeSub(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError):
        routing_result(**{field: subclass})
    with pytest.raises(ValueError):
        make_snapshot(**{field: subclass})
