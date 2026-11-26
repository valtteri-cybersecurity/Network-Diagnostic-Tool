import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, call

import pytest

import json

from network_diagnostic import diagnostic_service
from network_diagnostic.core.snapshot_serialization import snapshot_to_dict
from network_diagnostic.core.exceptions import (
    CommandError, CommandExecutionError, CommandNotFoundError, CommandTimeoutError,
    DiagnosticError, ParsingError, ValidationError,
)
from network_diagnostic.core.models import (
    COLLECTION_ISSUE_MESSAGES, CollectionStatus, DiagnosticSnapshot,
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus, LatencyAnalysis,
    ListeningPortReport, ListeningSocket, NetworkInterface, PacketLossStatus, PingResult,
    RoutingSnapshot, ServiceInfo, SocketCounts,
)


FIXTURES = Path(__file__).parent / "fixtures"


def completed(stdout, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


# get_connection_status

@pytest.mark.parametrize("connected", [True, False])
def test_get_connection_status_returns_networkmanager_state(connected, monkeypatch):
    status = Mock(return_value=connected)
    monkeypatch.setattr(diagnostic_service.network, "get_network_plugin", status)

    result = diagnostic_service.get_connection_status()

    assert result is connected
    status.assert_called_once_with()


@pytest.mark.parametrize("error_type", [CommandNotFoundError, CommandTimeoutError, ParsingError])
def test_get_connection_status_propagates_errors(error_type, monkeypatch):
    monkeypatch.setattr(
        diagnostic_service.network, "get_network_plugin", Mock(side_effect=error_type("nmcli unavailable")),
    )

    with pytest.raises(error_type, match="nmcli unavailable"):
        diagnostic_service.get_connection_status()


# run_ping_diagnostic

def test_run_ping_diagnostic_returns_engine_report_unchanged(monkeypatch):
    report = PingResult(target="gateway", execution_status=ExecutionStatus.COMPLETED)
    diagnostic = Mock(return_value=report)
    monkeypatch.setattr(diagnostic_service.ping, "run_ping_diagnostic", diagnostic)

    result = diagnostic_service.run_ping_diagnostic("gateway")

    assert result is report
    diagnostic.assert_called_once_with("gateway")


def test_run_ping_diagnostic_returns_analysed_measurement(subprocess_run):
    raw = (FIXTURES / "ping" / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(raw)

    result = diagnostic_service.run_ping_diagnostic("internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.reachable is True
    assert result.packet_loss_status is PacketLossStatus.NO_LOSS
    assert result.latency_analysis == "OK"
    assert result.rtt_range_analysis == "Good"
    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "3", "-W", "2", "8.8.8.8"]


def test_run_ping_diagnostic_keeps_total_loss_as_completed_negative_result(subprocess_run):
    raw = (FIXTURES / "ping" / "total_loss.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(raw, code=1)

    result = diagnostic_service.run_ping_diagnostic("internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.reachable is False
    assert result.packet_loss_status is PacketLossStatus.TOTAL_LOSS
    assert result.icmp_errors == 3
    assert result.avg_rtt_ms is None
    assert result.latency_analysis is None


@pytest.mark.parametrize("target, error", [
    ("router", "Unknown ping target: router"),
    (None, "Invalid ping target type: NoneType"),
])
def test_run_ping_diagnostic_reports_invalid_target_as_failed_result(target, error, subprocess_run):
    result = diagnostic_service.run_ping_diagnostic(target)

    assert result.execution_status is ExecutionStatus.FAILED
    assert result.error == error
    assert result.reachable is None
    subprocess_run.assert_not_called()


def test_run_ping_diagnostic_reports_timeout_without_raising(subprocess_run):
    subprocess_run.side_effect = subprocess.TimeoutExpired(["ping"], 10, output=b"partial\n")

    result = diagnostic_service.run_ping_diagnostic("localhost")

    assert result.execution_status is ExecutionStatus.TIMEOUT
    assert result.error == "ping timed out after 10 seconds."
    assert result.raw_output == "partial\n"
    assert result.packets_received is None


# run_dns_query

@pytest.mark.parametrize("arguments, hostname", [((), "google.com"), (("example.org",), "example.org")])
def test_run_dns_query_resolves_default_or_given_hostname(arguments, hostname, monkeypatch):
    report = DnsQueryResult(hostname=hostname, execution_status=ExecutionStatus.COMPLETED, resolved=True)
    query = Mock(return_value=report)
    monkeypatch.setattr(diagnostic_service.dns, "dns_query", query)

    result = diagnostic_service.run_dns_query(*arguments)

    assert result is report
    query.assert_called_once_with(hostname)


def test_run_dns_query_returns_unresolved_name_as_completed_result(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed("", code=2)

    result = diagnostic_service.run_dns_query("missing.example")

    assert result == DnsQueryResult(
        hostname="missing.example", execution_status=ExecutionStatus.COMPLETED, resolved=False,
    )


def test_run_dns_query_rejects_invalid_hostname_before_command(subprocess_run):
    with pytest.raises(ValidationError):
        diagnostic_service.run_dns_query("-x")

    subprocess_run.assert_not_called()


def test_run_dns_query_propagates_command_error(subprocess_run):
    subprocess_run.side_effect = FileNotFoundError("missing")

    with pytest.raises(CommandNotFoundError, match="Required command not found: getent"):
        diagnostic_service.run_dns_query()


# run_dns_latency_test

@pytest.mark.parametrize("latency", [0.0, 12.0])
def test_run_dns_latency_test_classifies_measured_latency(latency, monkeypatch):
    report = DnsLatencyResult(
        hostname="google.com", execution_status=ExecutionStatus.COMPLETED, dns_latency_ms=latency,
    )
    classification = LatencyAnalysis(latency_ms=latency, status="EXCELLENT")
    measure = Mock(return_value=report)
    classify = Mock(return_value=classification)
    monkeypatch.setattr(diagnostic_service.dns, "dns_latency", measure)
    monkeypatch.setattr(diagnostic_service.analysis, "dns_latency_analyzation", classify)

    result = diagnostic_service.run_dns_latency_test()

    assert result is report
    assert result.latency_analysis is classification
    measure.assert_called_once_with("google.com")
    classify.assert_called_once_with(report)


@pytest.mark.parametrize("status", [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT])
def test_run_dns_latency_test_does_not_classify_missing_latency(status, monkeypatch):
    report = DnsLatencyResult(hostname="example.org", execution_status=status)
    classify = Mock(side_effect=AssertionError("Missing latency must not be classified"))
    monkeypatch.setattr(diagnostic_service.dns, "dns_latency", Mock(return_value=report))
    monkeypatch.setattr(diagnostic_service.analysis, "dns_latency_analyzation", classify)

    result = diagnostic_service.run_dns_latency_test("example.org")

    assert result is report
    assert result.dns_latency_ms is None
    assert result.latency_analysis is None
    classify.assert_not_called()


def test_run_dns_latency_test_keeps_nxdomain_separate_from_fast_response(subprocess_run):
    raw = (FIXTURES / "dns" / "dig_nxdomain.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(raw)

    result = diagnostic_service.run_dns_latency_test("missing.example")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.dns_status == "NXDOMAIN"
    assert result.resolved is False
    assert result.latency_analysis == LatencyAnalysis(latency_ms=result.dns_latency_ms, status="EXCELLENT")


def test_run_dns_latency_test_returns_no_reply_as_timeout_result(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(";; connection timed out; no servers could be reached\n", code=9)

    result = diagnostic_service.run_dns_latency_test()

    assert result.execution_status is ExecutionStatus.TIMEOUT
    assert result.error == "No reply from DNS server"
    assert result.resolved is None
    assert result.latency_analysis is None


def test_run_dns_latency_test_rejects_invalid_hostname_before_command(subprocess_run):
    with pytest.raises(ValidationError):
        diagnostic_service.run_dns_latency_test("+trace")

    subprocess_run.assert_not_called()


def test_run_dns_latency_test_propagates_command_error(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed("", code=10, stderr="internal error")

    with pytest.raises(CommandExecutionError):
        diagnostic_service.run_dns_latency_test()


# lookup_ip_addresses

def test_lookup_ip_addresses_checks_default_hostnames_in_order(monkeypatch):
    hostnames = ["google.com", "wikipedia.com", "example.com", "yle.fi"]
    reports = [DnsAddressResult(hostname=name, execution_status=ExecutionStatus.COMPLETED) for name in hostnames]
    check = Mock(side_effect=reports)
    monkeypatch.setattr(diagnostic_service.dns, "ipv_check", check)

    result = diagnostic_service.lookup_ip_addresses()

    assert result == reports
    assert check.call_args_list == [call(name) for name in hostnames]


@pytest.mark.parametrize("hostnames", [["example.org"], ("example.org", "192.0.2.1"), [], ()])
def test_lookup_ip_addresses_accepts_list_or_tuple(hostnames, monkeypatch):
    check = Mock(side_effect=lambda name: DnsAddressResult(name, ExecutionStatus.COMPLETED))
    monkeypatch.setattr(diagnostic_service.dns, "ipv_check", check)

    result = diagnostic_service.lookup_ip_addresses(hostnames)

    assert [item.hostname for item in result] == list(hostnames)
    assert check.call_count == len(hostnames)


@pytest.mark.parametrize("hostnames", [
    ["example.org", "-f", "example.com"],
    ["example.org", None],
    ["example.org", "bad host"],
])
def test_lookup_ip_addresses_validates_all_hostnames_before_any_command(hostnames, monkeypatch, subprocess_run):
    check = Mock(side_effect=AssertionError("No lookup may run"))
    monkeypatch.setattr(diagnostic_service.dns, "ipv_check", check)

    with pytest.raises(ValidationError):
        diagnostic_service.lookup_ip_addresses(hostnames)

    check.assert_not_called()
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("hostnames", ["example.org", None, {"example.org"}])
def test_lookup_ip_addresses_rejects_non_sequence_input(hostnames, subprocess_run):
    with pytest.raises(ValidationError, match="hostnames must be a list or tuple of strings"):
        diagnostic_service.lookup_ip_addresses(hostnames)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("error, status", [
    (CommandTimeoutError("getent timed out after 10 seconds."), ExecutionStatus.TIMEOUT),
    (CommandNotFoundError("Required command not found: getent"), ExecutionStatus.FAILED),
    (CommandExecutionError("getent failed: exit code 3"), ExecutionStatus.FAILED),
    (ParsingError("Invalid address in getent output"), ExecutionStatus.FAILED),
])
def test_lookup_ip_addresses_keeps_other_results_when_one_lookup_fails(error, status, monkeypatch):
    first = DnsAddressResult("first.example", ExecutionStatus.COMPLETED, ["192.0.2.1"], [])
    last = DnsAddressResult("last.example", ExecutionStatus.COMPLETED, [], ["2001:db8::1"])
    check = Mock(side_effect=[first, error, last])
    monkeypatch.setattr(diagnostic_service.dns, "ipv_check", check)

    result = diagnostic_service.lookup_ip_addresses(["first.example", "broken.example", "last.example"])

    assert result[0] is first
    assert result[2] is last
    assert result[1] == DnsAddressResult(
        hostname="broken.example", execution_status=status, error=str(error),
    )
    assert check.call_count == 3


def test_lookup_ip_addresses_does_not_hide_programming_errors(monkeypatch):
    monkeypatch.setattr(diagnostic_service.dns, "ipv_check", Mock(side_effect=TypeError("bug")))

    with pytest.raises(TypeError, match="bug"):
        diagnostic_service.lookup_ip_addresses(["example.org"])


def test_lookup_ip_addresses_continues_after_real_command_timeout(subprocess_run):
    def respond(arguments, **kwargs):
        if arguments[2] == "slow.example":
            raise subprocess.TimeoutExpired(arguments, 10)
        if arguments[1] == "ahostsv4":
            return completed("192.0.2.10 STREAM fast.example\n")
        return completed("", code=2)

    subprocess_run.side_effect = respond

    result = diagnostic_service.lookup_ip_addresses(["slow.example", "fast.example"])

    assert result == [
        DnsAddressResult(
            hostname="slow.example", execution_status=ExecutionStatus.TIMEOUT,
            error="getent timed out after 10 seconds.",
        ),
        DnsAddressResult("fast.example", ExecutionStatus.COMPLETED, ["192.0.2.10"], []),
    ]


# get_listening_ports

def test_get_listening_ports_builds_report_from_one_socket_listing(monkeypatch):
    raw = ["tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"]
    rows = [ListeningSocket(protocol="tcp", state="LISTEN", local_port="22")]
    sockets = [ListeningSocket(protocol="TCP", state="LISTEN", local_port="22")]
    counts = SocketCounts(tcp_count=1)
    services = [ServiceInfo(protocol="TCP", port=22, service_name="SSH")]
    collect = Mock(return_value=raw)
    parse = Mock(return_value=rows)
    select = Mock(return_value=sockets)
    count = Mock(return_value=counts)
    analyze = Mock(return_value=services)
    monkeypatch.setattr(diagnostic_service.network, "get_listening_sockets", collect)
    monkeypatch.setattr(diagnostic_service.parsers, "parse_ss_output", parse)
    monkeypatch.setattr(diagnostic_service.analysis, "get_open_ports", select)
    monkeypatch.setattr(diagnostic_service.analysis, "count_open_ports", count)
    monkeypatch.setattr(diagnostic_service.analysis, "analyze_open_ports", analyze)

    result = diagnostic_service.get_listening_ports()

    assert result == ListeningPortReport(sockets=sockets, counts=counts, services=services)
    collect.assert_called_once_with()
    parse.assert_called_once_with(raw)
    select.assert_called_once_with(rows)
    count.assert_called_once_with(sockets)
    analyze.assert_called_once_with(sockets)


def test_get_listening_ports_integrates_real_socket_output(subprocess_run):
    raw = (FIXTURES / "network" / "ss_sockets.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(raw)

    result = diagnostic_service.get_listening_ports()

    assert result.counts.tcp_count == sum(1 for item in result.sockets if item.protocol == "TCP")
    assert result.counts.udp_count == sum(1 for item in result.sockets if item.protocol == "UDP")
    assert len(result.services) == len(result.sockets)
    assert all(isinstance(item, ServiceInfo) for item in result.services)
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["ss", "-H", "-tuln"]


def test_get_listening_ports_returns_empty_report_for_no_sockets(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed("")

    result = diagnostic_service.get_listening_ports()

    assert result == ListeningPortReport()


def test_get_listening_ports_does_not_return_partial_list_for_invalid_line(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(
        "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
        "tcp LISTEN 0 128 not-an-address:80 0.0.0.0:*\n"
    )

    with pytest.raises(ParsingError, match="Unrecognized line in ss output"):
        diagnostic_service.get_listening_ports()


def test_get_listening_ports_propagates_command_error(subprocess_run):
    subprocess_run.side_effect = FileNotFoundError("missing")

    with pytest.raises(CommandNotFoundError, match="Required command not found: ss"):
        diagnostic_service.get_listening_ports()


# get_routing_snapshot

def test_get_routing_snapshot_reads_routing_table_once_and_shares_it(monkeypatch):
    routes = ["default via 192.0.2.1 dev enp3s0 src 192.0.2.10"]
    collect = Mock(return_value=routes)
    hostname = Mock(return_value="workstation")
    interface = Mock(return_value="enp3s0")
    address = Mock(return_value="192.0.2.10")
    gateway = Mock(return_value="192.0.2.1")
    monkeypatch.setattr(diagnostic_service.network, "get_ip_route", collect)
    monkeypatch.setattr(diagnostic_service.network, "get_hostname", hostname)
    monkeypatch.setattr(diagnostic_service.network, "get_nic", interface)
    monkeypatch.setattr(diagnostic_service.network, "get_ip", address)
    monkeypatch.setattr(diagnostic_service.network, "get_gateway", gateway)

    result = diagnostic_service.get_routing_snapshot()

    assert result == RoutingSnapshot(
        hostname="workstation", interface="enp3s0", ip_address="192.0.2.10",
        gateway="192.0.2.1", routes=routes, errors={},
    )
    collect.assert_called_once_with()
    hostname.assert_called_once_with()
    interface.assert_called_once_with(routes)
    address.assert_called_once_with(routes)
    gateway.assert_called_once_with(routes)


def test_get_routing_snapshot_integrates_route_commands(subprocess_run):
    route_output = (FIXTURES / "network" / "ip_route.txt").read_text(encoding="utf-8")
    responses = {
        ("ip", "-4", "route"): completed(route_output),
        ("hostname",): completed("workstation\n"),
        ("ip", "-4", "-o", "addr", "show", "dev", "enp3s0"): completed(
            (FIXTURES / "network" / "ip_address.txt").read_text(encoding="utf-8"),
        ),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[tuple(arguments)]

    result = diagnostic_service.get_routing_snapshot()

    assert result == RoutingSnapshot(
        hostname="workstation", interface="enp3s0", ip_address="192.0.2.10", gateway="192.0.2.1",
        routes=route_output.strip().splitlines(), errors={},
    )
    assert [item.args[0] for item in subprocess_run.call_args_list] == [
        ["ip", "-4", "route"], ["hostname"], ["ip", "-4", "-o", "addr", "show", "dev", "enp3s0"],
    ]


def test_get_routing_snapshot_keeps_hostname_when_routing_table_fails(monkeypatch):
    route_reader = Mock(side_effect=AssertionError("Route values need the routing table"))
    monkeypatch.setattr(
        diagnostic_service.network, "get_ip_route", Mock(side_effect=CommandNotFoundError("Required command not found: ip")),
    )
    monkeypatch.setattr(diagnostic_service.network, "get_hostname", Mock(return_value="workstation"))
    monkeypatch.setattr(diagnostic_service.network, "get_nic", route_reader)
    monkeypatch.setattr(diagnostic_service.network, "get_ip", route_reader)
    monkeypatch.setattr(diagnostic_service.network, "get_gateway", route_reader)

    result = diagnostic_service.get_routing_snapshot()

    assert result == RoutingSnapshot(
        hostname="workstation", routes=None, errors={"routes": "Required command not found: ip"},
    )
    route_reader.assert_not_called()


def test_get_routing_snapshot_keeps_route_values_when_hostname_fails(subprocess_run):
    def respond(arguments, **kwargs):
        if arguments[0] == "hostname":
            raise subprocess.TimeoutExpired(arguments, 10)
        return completed("default via 192.0.2.1 dev enp3s0 src 192.0.2.10\n")

    subprocess_run.side_effect = respond

    result = diagnostic_service.get_routing_snapshot()

    assert result == RoutingSnapshot(
        hostname=None, interface="enp3s0", ip_address="192.0.2.10", gateway="192.0.2.1",
        routes=["default via 192.0.2.1 dev enp3s0 src 192.0.2.10"],
        errors={"hostname": "hostname timed out after 10 seconds."},
    )


def test_get_routing_snapshot_records_invalid_route_value_and_keeps_others(subprocess_run):
    responses = {
        "ip": completed("default via 192.0.2.1 dev -f src 192.0.2.10\n"),
        "hostname": completed("workstation\n"),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]

    result = diagnostic_service.get_routing_snapshot()

    assert result.hostname == "workstation"
    assert result.interface is None
    assert result.ip_address == "192.0.2.10"
    assert result.gateway == "192.0.2.1"
    assert result.errors == {"interface": "Invalid default route interface: Invalid interface name: '-f'"}
    assert [item.args[0] for item in subprocess_run.call_args_list] == [["ip", "-4", "route"], ["hostname"]]


def test_get_routing_snapshot_records_address_command_failure(subprocess_run):
    def respond(arguments, **kwargs):
        if arguments[0] == "hostname":
            return completed("workstation\n")
        if arguments[:2] == ["ip", "-4"] and arguments[2] == "route":
            return completed("default via 192.0.2.1 dev enp3s0\n")
        return completed("", code=1, stderr="Device does not exist")

    subprocess_run.side_effect = respond

    result = diagnostic_service.get_routing_snapshot()

    assert result.interface == "enp3s0"
    assert result.gateway == "192.0.2.1"
    assert result.ip_address is None
    assert list(result.errors) == ["ip_address"]
    assert "Device does not exist" in result.errors["ip_address"]


def test_get_routing_snapshot_reports_missing_default_route_without_errors(subprocess_run):
    responses = {"ip": completed(""), "hostname": completed("workstation\n")}
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]

    result = diagnostic_service.get_routing_snapshot()

    assert result == RoutingSnapshot(hostname="workstation", routes=[], errors={})


def test_get_routing_snapshot_does_not_hide_programming_errors(monkeypatch):
    monkeypatch.setattr(diagnostic_service.network, "get_ip_route", Mock(return_value=[]))
    monkeypatch.setattr(diagnostic_service.network, "get_hostname", Mock(return_value="workstation"))
    monkeypatch.setattr(diagnostic_service.network, "get_nic", Mock(side_effect=AttributeError("bug")))

    with pytest.raises(AttributeError, match="bug"):
        diagnostic_service.get_routing_snapshot()


def test_get_routing_snapshot_returns_independent_results(monkeypatch):
    monkeypatch.setattr(
        diagnostic_service.network, "get_ip_route", Mock(side_effect=CommandError("ip failed")),
    )
    monkeypatch.setattr(diagnostic_service.network, "get_hostname", Mock(return_value="workstation"))

    first = diagnostic_service.get_routing_snapshot()
    second = diagnostic_service.get_routing_snapshot()

    assert first == second
    assert first is not second
    assert first.errors is not second.errors


# get_network_interfaces

def test_get_network_interfaces_parses_networkmanager_rows(monkeypatch):
    raw = ["enp3s0:ethernet:connected:Wired connection 1"]
    interfaces = [NetworkInterface("enp3s0", "ethernet", "connected", "Wired connection 1")]
    collect = Mock(return_value=raw)
    parse = Mock(return_value=interfaces)
    monkeypatch.setattr(diagnostic_service.network, "nmcli_status", collect)
    monkeypatch.setattr(diagnostic_service.parsers, "parse_nmcli_output", parse)

    result = diagnostic_service.get_network_interfaces()

    assert result is interfaces
    collect.assert_called_once_with()
    parse.assert_called_once_with(raw)


def test_get_network_interfaces_integrates_nmcli_output(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed(
        "enp3s0:ethernet:connected (externally):Wired connection 1\nlo:loopback:unmanaged:\n",
    )

    result = diagnostic_service.get_network_interfaces()

    assert result[0] == NetworkInterface("enp3s0", "ethernet", "connected (externally)", "Wired connection 1")
    assert result[1].device == "lo"
    subprocess_run.assert_called_once()


@pytest.mark.parametrize("error_type", [CommandNotFoundError, CommandTimeoutError, DiagnosticError])
def test_get_network_interfaces_propagates_errors(error_type, monkeypatch):
    monkeypatch.setattr(diagnostic_service.network, "nmcli_status", Mock(side_effect=error_type("nmcli failed")))

    with pytest.raises(error_type, match="nmcli failed"):
        diagnostic_service.get_network_interfaces()


def test_get_routing_snapshot_records_multipath_default_route_as_error(monkeypatch):
    # Phase 13 regression: a multipath default route left the interface, address
    # and gateway empty without any error, as if they did not exist.
    routes = ["default proto static metric 100", "\tnexthop via 192.0.2.1 dev enp3s0 weight 1"]
    monkeypatch.setattr(diagnostic_service.network, "get_ip_route", Mock(return_value=routes))
    monkeypatch.setattr(diagnostic_service.network, "get_hostname", Mock(return_value="host"))

    result = diagnostic_service.get_routing_snapshot()

    message = "Unsupported default route: no single output interface (for example, a multipath route)"
    assert result.routes == routes
    assert (result.interface, result.ip_address, result.gateway) == (None, None, None)
    assert result.errors == {"interface": message, "ip_address": message, "gateway": message}


def test_get_connection_status_reports_unrecognized_nmcli_output_as_error(subprocess_run):
    # Phase 13 regression: unrecognized lines were skipped, so unknown output
    # was reported as "no connected interface" instead of an error.
    subprocess_run.side_effect = None
    subprocess_run.return_value = completed("enp3s0 connected\n")

    with pytest.raises(ParsingError, match="^Unrecognized line in nmcli output$"):
        diagnostic_service.get_connection_status()


# collect_diagnostic_snapshot

COMPLETE = ExecutionStatus.COMPLETED
S = CollectionStatus
SNAPSHOT_ID = "3f2b8c1e-9d4a-4b6e-8f10-2a7c5d9e1b34"
BASE_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
WALL_STEP = timedelta(seconds=1)
MONOTONIC_STEP = 0.25
ALIASES = ("localhost", "gateway", "internet", "google.com")
DEFAULT_HOSTS = ("google.com", "wikipedia.com", "example.com", "yle.fi")
REAL_HELPERS = {
    name: getattr(diagnostic_service, name) for name in ("_new_snapshot_id", "_utc_now", "_monotonic")
}
DEFAULT_CALLS = (
    [("routing",), ("interfaces",), ("sockets",)]
    + [("ping", alias) for alias in ALIASES]
    + [("dns_query", "google.com"), ("dns_latency", "google.com")]
    + [("ipv_check", host) for host in DEFAULT_HOSTS]
)


def routing_source():
    return RoutingSnapshot(
        hostname="host", interface="eth0", ip_address="192.168.1.5", gateway="192.168.1.1",
        routes=["default via 192.168.1.1 dev eth0"],
    )


def interfaces_source():
    return [NetworkInterface("eth0", "ethernet", "connected", "Wired"), NetworkInterface("lo", "loopback")]


def sockets_source():
    sockets = [ListeningSocket("tcp", "LISTEN", "127.0.0.1", "22", "0.0.0.0", "*")]
    return ListeningPortReport(
        sockets=sockets, counts=SocketCounts(tcp_count=1),
        services=[ServiceInfo("tcp", "127.0.0.1", 22, "ssh")],
    )


def ping_source(alias, **fields):
    values = {
        "target": alias, "execution_status": COMPLETE, "packets_sent": 4, "packets_received": 4,
        "packet_loss_percent": 0.0, "min_rtt_ms": 1.0, "avg_rtt_ms": 2.0, "max_rtt_ms": 3.0, "mdev_ms": 0.5,
        "reachable": True, "packet_loss_status": PacketLossStatus.NO_LOSS, "icmp_errors": 0,
    }
    values.update(fields)
    return PingResult(**values)


def query_source(hostname, **fields):
    values = {"hostname": hostname, "execution_status": COMPLETE, "resolved": True, "ip_address": "93.184.216.34"}
    values.update(fields)
    return DnsQueryResult(**values)


def latency_source(hostname, **fields):
    values = {
        "hostname": hostname, "execution_status": COMPLETE, "dns_status": "NOERROR", "resolved": True,
        "dns_latency_ms": 12.5, "answer_count": 1,
    }
    values.update(fields)
    return DnsLatencyResult(**values)


def addresses_source(hostname, **fields):
    values = {
        "hostname": hostname, "execution_status": COMPLETE,
        "ipv4_addresses": ["93.184.216.34"], "ipv6_addresses": ["2001:db8::1"],
    }
    values.update(fields)
    return DnsAddressResult(**values)


class Harness:
    """Replace every diagnostic function and clock; record the call order."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.linux = True
        self.wall_direction = 1
        self.wall_ticks = 0
        self.monotonic_ticks = 0
        self.behaviors = {
            "routing": lambda: routing_source(),
            "interfaces": lambda: interfaces_source(),
            "sockets": lambda: sockets_source(),
            "ping": ping_source,
            "dns_query": query_source,
            "dns_latency": latency_source,
            "ipv_check": addresses_source,
        }
        targets = {
            "routing": (diagnostic_service, "get_routing_snapshot"),
            "interfaces": (diagnostic_service, "get_network_interfaces"),
            "sockets": (diagnostic_service, "get_listening_ports"),
            "ping": (diagnostic_service, "run_ping_diagnostic"),
            "dns_query": (diagnostic_service.dns, "dns_query"),
            "dns_latency": (diagnostic_service.dns, "dns_latency"),
            "ipv_check": (diagnostic_service.dns, "ipv_check"),
        }
        for name, (owner, attribute) in targets.items():
            monkeypatch.setattr(owner, attribute, self.recorder(name))
        monkeypatch.setattr(diagnostic_service, "_is_linux", lambda: self.linux)
        monkeypatch.setattr(diagnostic_service, "_new_snapshot_id", lambda: SNAPSHOT_ID)
        monkeypatch.setattr(diagnostic_service, "_utc_now", self.utc_now)
        monkeypatch.setattr(diagnostic_service, "_monotonic", self.monotonic)

    def recorder(self, name):
        def call(*arguments):
            self.calls.append((name, *arguments))
            return self.behaviors[name](*arguments)
        return call

    def utc_now(self):
        moment = BASE_TIME + self.wall_direction * self.wall_ticks * WALL_STEP
        self.wall_ticks += 1
        return moment

    def monotonic(self):
        value = self.monotonic_ticks * MONOTONIC_STEP
        self.monotonic_ticks += 1
        return value

    def returns(self, name, value):
        self.behaviors[name] = lambda *arguments: value

    def raises(self, name, error, only=None):
        """Raise for every call of one operation, or only when the first argument matches."""
        default = self.behaviors[name]

        def behavior(*arguments):
            if only is None or arguments[0] == only:
                raise error
            return default(*arguments)
        self.behaviors[name] = behavior

    def transform(self, name, change):
        """Change the legacy object that the default behavior returns."""
        default = self.behaviors[name]

        def behavior(*arguments):
            value = default(*arguments)
            change(value)
            return value
        self.behaviors[name] = behavior


@pytest.fixture
def harness(monkeypatch):
    return Harness(monkeypatch)


def sections(snapshot):
    """All results of a snapshot in collection order."""
    return [
        snapshot.routing, snapshot.interfaces, snapshot.listening_sockets, *snapshot.pings,
        snapshot.dns_query, snapshot.dns_latency, *snapshot.dns_addresses,
    ]


def issue_pairs(result):
    return [(issue.code, issue.field) for issue in result.issues]


def assert_not_performed(result, status, code):
    assert result.status is status
    assert result.value is None
    assert issue_pairs(result) == [(code, None)]
    assert result.issues[0].message == COLLECTION_ISSUE_MESSAGES[code]
    assert (result.started_at, result.finished_at, result.duration_seconds) == (None, None, None)


def assert_failed_attempt(result, status, code):
    assert result.status is status
    assert result.value is None
    assert issue_pairs(result) == [(code, None)]
    assert result.started_at is not None and result.finished_at is not None
    assert result.duration_seconds == MONOTONIC_STEP


# --- Requests and call order ---------------------------------------------------


def test_default_request_collects_everything_in_the_documented_order(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert harness.calls == DEFAULT_CALLS
    assert isinstance(snapshot, DiagnosticSnapshot)
    assert snapshot.schema_version == 1 and snapshot.snapshot_id == SNAPSHOT_ID
    assert [r.operation_id for r in sections(snapshot)] == (
        ["routing", "interfaces", "listening_sockets"]
        + [f"ping.{alias}" for alias in ALIASES] + ["dns.query", "dns.latency"]
        + [f"dns.addresses:{host}" for host in DEFAULT_HOSTS]
    )
    assert all(r.status is S.COMPLETED and r.issues == () for r in sections(snapshot))
    assert [r.target for r in snapshot.pings] == list(ALIASES)
    assert [r.target for r in snapshot.dns_addresses] == list(DEFAULT_HOSTS)
    assert snapshot.routing.target is None and snapshot.dns_query.target == "google.com"


def test_collected_values_hold_the_observations(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.routing.value == RoutingSnapshot(
        hostname="host", interface="eth0", ip_address="192.168.1.5", gateway="192.168.1.1",
        routes=["default via 192.168.1.1 dev eth0"],
    )
    assert snapshot.interfaces.value == interfaces_source()
    assert snapshot.listening_sockets.value == sockets_source().sockets
    assert snapshot.pings[1].value == ping_source("gateway")
    assert snapshot.dns_query.value == query_source("google.com")
    assert snapshot.dns_latency.value == latency_source("google.com")
    assert snapshot.dns_addresses[3].value == addresses_source("yle.fi")


def test_custom_request_keeps_the_requested_order_and_targets(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot(
        ping_targets=["internet", "localhost"], dns_hostname="example.com",
        address_hostnames=["yle.fi", "2001:db8::1"], include_interfaces=False,
    )

    assert harness.calls == [
        ("routing",), ("sockets",), ("ping", "internet"), ("ping", "localhost"),
        ("dns_query", "example.com"), ("dns_latency", "example.com"),
        ("ipv_check", "yle.fi"), ("ipv_check", "2001:db8::1"),
    ]
    assert [r.target for r in snapshot.pings] == ["internet", "localhost"]
    assert [r.operation_id for r in snapshot.dns_addresses] == ["dns.addresses:yle.fi", "dns.addresses:2001:db8::1"]
    assert snapshot.dns_latency.target == "example.com"


def test_local_only_request_runs_no_ping_and_no_dns(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot(
        ping_targets=(), address_hostnames=(), include_dns_query=False, include_dns_latency=False,
    )

    assert harness.calls == [("routing",), ("interfaces",), ("sockets",)]
    assert snapshot.pings == () and snapshot.dns_addresses == ()
    assert_not_performed(snapshot.dns_query, S.NOT_COLLECTED, "not_requested")
    assert_not_performed(snapshot.dns_latency, S.NOT_COLLECTED, "not_requested")


def test_empty_request_runs_nothing_and_still_builds_a_snapshot(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot(
        ping_targets=(), address_hostnames=(), include_routing=False, include_interfaces=False,
        include_listening_sockets=False, include_dns_query=False, include_dns_latency=False,
    )

    assert harness.calls == []
    assert all(r.status is S.NOT_COLLECTED for r in sections(snapshot))
    assert snapshot.duration_seconds == MONOTONIC_STEP


@pytest.mark.parametrize(
    ("flag", "attribute", "target"),
    [
        ("include_routing", "routing", None),
        ("include_interfaces", "interfaces", None),
        ("include_listening_sockets", "listening_sockets", None),
        ("include_dns_query", "dns_query", "wikipedia.com"),
        ("include_dns_latency", "dns_latency", "wikipedia.com"),
    ],
)
def test_disabled_section_is_not_collected_without_timing(harness, flag, attribute, target):
    snapshot = diagnostic_service.collect_diagnostic_snapshot(dns_hostname="wikipedia.com", **{flag: False})

    result = getattr(snapshot, attribute)
    assert_not_performed(result, S.NOT_COLLECTED, "not_requested")
    assert result.target == target
    assert len(harness.calls) == len(DEFAULT_CALLS) - 1
    assert sum(r.status is S.NOT_COLLECTED for r in sections(snapshot)) == 1


# --- Validation ----------------------------------------------------------------


class StrSubclass(str):
    pass


INVALID_REQUESTS = [
    {"ping_targets": ("gateway", "gateway")},
    {"ping_targets": ("localhost", "gateway", "internet", "google.com", "localhost")},
    {"ping_targets": ("example.com",)},
    {"ping_targets": ("Gateway",)},
    {"ping_targets": ("gateway", "")},
    {"ping_targets": (5,)},
    {"ping_targets": (None,)},
    {"ping_targets": (["gateway"],)},
    {"ping_targets": ({"gateway"},)},
    {"ping_targets": ({},)},
    {"ping_targets": (StrSubclass("gateway"),)},
    {"ping_targets": "gateway"},
    {"ping_targets": {"gateway"}},
    {"ping_targets": iter(["gateway"])},
    {"ping_targets": None},
    {"address_hostnames": ("a.example", "a.example")},
    {"address_hostnames": tuple(f"h{i}.example" for i in range(5))},
    {"address_hostnames": ("-f",)},
    {"address_hostnames": ("+trace",)},
    {"address_hostnames": ("a b",)},
    {"address_hostnames": ("bad\n",)},
    {"address_hostnames": ("",)},
    {"address_hostnames": ("a" * 255,)},
    {"address_hostnames": ("exämple.com",)},
    {"address_hostnames": ("a/b",)},
    {"address_hostnames": (5,)},
    {"address_hostnames": (None,)},
    {"address_hostnames": (["a.example"],)},
    {"address_hostnames": ({"a.example"},)},
    {"address_hostnames": ({},)},
    {"address_hostnames": (StrSubclass("a.example"),)},
    {"address_hostnames": "google.com"},
    {"address_hostnames": {"google.com"}},
    {"address_hostnames": None},
    {"dns_hostname": "-f"},
    {"dns_hostname": "a b"},
    {"dns_hostname": ""},
    {"dns_hostname": "a" * 255},
    {"dns_hostname": 5},
    {"dns_hostname": None},
    {"dns_hostname": ["google.com"]},
    {"dns_hostname": StrSubclass("google.com")},
    {"include_routing": 1},
    {"include_interfaces": None},
    {"include_listening_sockets": "yes"},
    {"include_dns_query": 0},
    {"include_dns_latency": 1.0},
    {"dns_hostname": "-f", "include_dns_query": False, "include_dns_latency": False},
]


@pytest.mark.parametrize("request_arguments", INVALID_REQUESTS, ids=repr)
@pytest.mark.parametrize("linux", [True, False])
def test_invalid_request_raises_validation_error_before_any_call(harness, request_arguments, linux):
    harness.linux = linux

    with pytest.raises(ValidationError):
        diagnostic_service.collect_diagnostic_snapshot(**request_arguments)

    assert harness.calls == []
    assert harness.wall_ticks == 0 and harness.monotonic_ticks == 0


def test_collector_takes_keyword_arguments_only(harness):
    with pytest.raises(TypeError):
        diagnostic_service.collect_diagnostic_snapshot(("gateway",))
    with pytest.raises(TypeError):
        diagnostic_service.collect_diagnostic_snapshot(unknown=True)


@pytest.mark.parametrize("hosts", [
    ("a.example", "b.example", "c.example", "d.example"),
    ("192.0.2.1", "2001:db8::1", "a-b.example", "A.Example"),
])
def test_four_valid_address_hostnames_are_accepted(harness, hosts):
    snapshot = diagnostic_service.collect_diagnostic_snapshot(address_hostnames=hosts)

    assert [r.target for r in snapshot.dns_addresses] == list(hosts)


def test_request_sequences_are_copied_before_collection(harness):
    targets = ["gateway"]
    hosts = ["a.example"]
    harness.behaviors["ping"] = lambda alias: (targets.append("internet"), hosts.append("b.example"),
                                              ping_source(alias))[2]

    snapshot = diagnostic_service.collect_diagnostic_snapshot(ping_targets=targets, address_hostnames=hosts)

    assert [r.target for r in snapshot.pings] == ["gateway"]
    assert [r.target for r in snapshot.dns_addresses] == ["a.example"]


# --- Exceptions mapped to results ----------------------------------------------

OPERATIONS = [
    ("routing", lambda s: s.routing),
    ("interfaces", lambda s: s.interfaces),
    ("sockets", lambda s: s.listening_sockets),
    ("ping", lambda s: s.pings[2]),
    ("dns_query", lambda s: s.dns_query),
    ("dns_latency", lambda s: s.dns_latency),
    ("ipv_check", lambda s: s.dns_addresses[2]),
]
OPERATION_IDS = [name for name, _ in OPERATIONS]
EXCEPTION_MAPPING = [
    (CommandNotFoundError("nmcli missing"), S.UNAVAILABLE, "command_unavailable"),
    (CommandTimeoutError("timed out"), S.TIMEOUT, "command_timeout"),
    (CommandExecutionError("exit 1"), S.FAILED, "command_failed"),
    (CommandError("generic"), S.FAILED, "command_failed"),
    (ParsingError("bad output"), S.FAILED, "parsing_failed"),
]


@pytest.mark.parametrize("name_and_section", OPERATIONS, ids=OPERATION_IDS)
@pytest.mark.parametrize(("error", "status", "code"), EXCEPTION_MAPPING, ids=lambda v: type(v).__name__)
def test_expected_errors_become_results_with_timing(harness, name_and_section, error, status, code):
    name, section = name_and_section
    harness.raises(name, error)

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    result = section(snapshot)
    assert_failed_attempt(result, status, code)
    assert COLLECTION_ISSUE_MESSAGES[code] == result.issues[0].message
    assert "nmcli" not in repr(result) and "exit 1" not in repr(result)
    assert harness.calls == DEFAULT_CALLS
    others = [r for r in sections(snapshot) if r is not result and r.operation_id != result.operation_id]
    assert any(r.status is S.COMPLETED for r in others)


@pytest.mark.parametrize("name_and_section", OPERATIONS, ids=OPERATION_IDS)
@pytest.mark.parametrize("error", [
    RuntimeError("bug"), ValueError("raised by the diagnostic function"), KeyError("k"),
    ValidationError("late validation"), TypeError("t"), OSError("o"),
], ids=lambda e: type(e).__name__)
def test_unexpected_errors_propagate_unchanged(harness, name_and_section, error):
    name, _ = name_and_section
    harness.raises(name, error)

    with pytest.raises(type(error)) as raised:
        diagnostic_service.collect_diagnostic_snapshot()

    assert raised.value is error


def test_one_failure_does_not_stop_later_operations(harness):
    harness.raises("interfaces", CommandTimeoutError("nmcli"))

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert harness.calls == DEFAULT_CALLS
    assert snapshot.interfaces.status is S.TIMEOUT
    assert snapshot.listening_sockets.status is S.COMPLETED
    assert all(r.status is S.COMPLETED for r in snapshot.pings)
    assert all(r.status is S.COMPLETED for r in snapshot.dns_addresses)


def test_several_failures_are_recorded_independently(harness):
    harness.raises("sockets", ParsingError("ss"))
    harness.raises("ping", CommandNotFoundError("ping"), only="gateway")
    harness.raises("ipv_check", CommandExecutionError("getent"), only="example.com")
    harness.returns("dns_query", query_source("google.com", execution_status=ExecutionStatus.TIMEOUT))

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert harness.calls == DEFAULT_CALLS
    assert [r.status.value for r in sections(snapshot)] == [
        "completed", "completed", "failed", "completed", "unavailable", "completed", "completed",
        "timeout", "completed", "completed", "completed", "failed", "completed",
    ]
    assert issue_pairs(snapshot.listening_sockets) == [("parsing_failed", None)]
    assert issue_pairs(snapshot.pings[1]) == [("command_unavailable", None)]
    assert issue_pairs(snapshot.dns_addresses[2]) == [("command_failed", None)]
    assert issue_pairs(snapshot.dns_query) == [("command_timeout", None)]


# --- Legacy execution status mapping --------------------------------------------

EXECUTION_SOURCES = [
    ("ping", lambda s: s.pings[0], lambda alias, **f: ping_source(alias, **f)),
    ("dns_query", lambda s: s.dns_query, query_source),
    ("dns_latency", lambda s: s.dns_latency, latency_source),
    ("ipv_check", lambda s: s.dns_addresses[0], addresses_source),
]


@pytest.mark.parametrize("source", EXECUTION_SOURCES, ids=[item[0] for item in EXECUTION_SOURCES])
@pytest.mark.parametrize(("execution", "status", "code"), [
    (ExecutionStatus.FAILED, S.FAILED, "measurement_failed"),
    (ExecutionStatus.TIMEOUT, S.TIMEOUT, "command_timeout"),
])
def test_failed_and_timed_out_observations_have_no_value(harness, source, execution, status, code):
    name, section, build = source
    harness.behaviors[name] = lambda host: build(host, execution_status=execution, error="raw failure text")

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert_failed_attempt(section(snapshot), status, code)
    assert "raw failure text" not in repr(snapshot)


# --- Routing ---------------------------------------------------------------------


def test_routing_without_errors_is_completed_with_an_empty_errors_dict(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.routing.status is S.COMPLETED
    assert snapshot.routing.value.errors == {}


def routing_with_errors(errors, **fields):
    value = RoutingSnapshot(errors=errors, **fields)
    return value


@pytest.mark.parametrize(
    ("legacy", "issues"),
    [
        (routing_with_errors({"routes": "ip route failed"}, hostname="host"),
         [("measurement_failed", "routes"), ("dependency_unavailable", "interface"),
          ("dependency_unavailable", "ip_address"), ("dependency_unavailable", "gateway")]),
        (routing_with_errors({"hostname": "no hostname"}, interface="eth0", ip_address="192.168.1.5",
                             gateway="192.168.1.1", routes=[]),
         [("measurement_failed", "hostname")]),
        (routing_with_errors({"gateway": "no gateway"}, hostname="host", interface="eth0",
                             ip_address="192.168.1.5", routes=[]),
         [("measurement_failed", "gateway")]),
        (routing_with_errors({"routes": "x", "hostname": "y"}, interface=None),
         None),
        (routing_with_errors({"hostname": "x", "interface": "y", "ip_address": "z", "gateway": "w"}, routes=[]),
         [("measurement_failed", "hostname"), ("measurement_failed", "interface"),
          ("measurement_failed", "ip_address"), ("measurement_failed", "gateway")]),
    ],
    ids=["routes_failed", "hostname_failed", "gateway_failed", "nothing_usable_with_routes_failed",
         "only_empty_routes_usable"],
)
def test_routing_errors_become_partial_or_failed(harness, legacy, issues):
    harness.returns("routing", legacy)

    result = diagnostic_service.collect_diagnostic_snapshot().routing

    if issues is None:
        assert result.status is S.FAILED and result.value is None
        assert issue_pairs(result) == [("measurement_failed", "hostname"), ("measurement_failed", "routes")]
    else:
        assert result.status is S.PARTIAL
        assert issue_pairs(result) == issues
        assert result.value.errors == {}
        assert result.value.hostname == legacy.hostname
    assert result.started_at is not None and result.duration_seconds == MONOTONIC_STEP


def test_routing_partial_value_is_detached_and_keeps_none_fields(harness):
    legacy = RoutingSnapshot(hostname="host", routes=None, errors={"routes": "text must not leak"})
    harness.returns("routing", legacy)

    result = diagnostic_service.collect_diagnostic_snapshot().routing

    assert result.value is not legacy
    assert (result.value.interface, result.value.ip_address, result.value.gateway, result.value.routes) == (
        None, None, None, None,
    )
    assert "text must not leak" not in repr(result)
    assert legacy.errors == {"routes": "text must not leak"}


def test_routing_with_nothing_usable_and_all_fields_failed_is_failed(harness):
    legacy = RoutingSnapshot(errors={name: "x" for name in ("hostname", "routes")})
    harness.returns("routing", legacy)

    result = diagnostic_service.collect_diagnostic_snapshot().routing

    assert result.status is S.FAILED
    assert [pair[1] for pair in issue_pairs(result)] == ["hostname", "routes"]


def test_routing_with_none_gateway_and_empty_routes_stays_completed(harness):
    harness.returns("routing", RoutingSnapshot(hostname="host", routes=[]))

    result = diagnostic_service.collect_diagnostic_snapshot().routing

    assert result.status is S.COMPLETED
    assert result.value.gateway is None and result.value.routes == []


# --- Valid observations that look bad but are data ------------------------------------


def test_total_packet_loss_is_a_completed_observation(harness):
    lost = dict(
        packets_received=0, packet_loss_percent=100.0, min_rtt_ms=None, avg_rtt_ms=None,
        max_rtt_ms=None, mdev_ms=None, reachable=False, packet_loss_status=PacketLossStatus.TOTAL_LOSS,
    )
    harness.behaviors["ping"] = lambda alias: ping_source(alias, **lost)

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert all(r.status is S.COMPLETED for r in snapshot.pings)
    value = snapshot.pings[0].value
    assert (value.packet_loss_percent, value.reachable, value.avg_rtt_ms) == (100.0, False, None)
    assert value.packet_loss_status is PacketLossStatus.TOTAL_LOSS


def test_zero_latency_and_missing_measurements_stay_completed(harness):
    harness.behaviors["ping"] = lambda alias: ping_source(
        alias, min_rtt_ms=0.0, avg_rtt_ms=0.0, max_rtt_ms=0.0, mdev_ms=0.0, icmp_errors=None,
        packets_sent=None, packets_received=None, packet_loss_percent=None, reachable=None,
    )
    harness.behaviors["dns_latency"] = lambda host: latency_source(
        host, dns_latency_ms=0.0, answer_count=0, dns_status=None, resolved=None,
    )

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.pings[0].status is S.COMPLETED and snapshot.pings[0].value.avg_rtt_ms == 0.0
    assert snapshot.dns_latency.status is S.COMPLETED
    assert snapshot.dns_latency.value.dns_latency_ms == 0.0


@pytest.mark.parametrize("dns_status", ["NXDOMAIN", "SERVFAIL"])
def test_negative_dns_answers_are_completed_observations(harness, dns_status):
    harness.behaviors["dns_query"] = lambda host: query_source(host, resolved=False, ip_address=None)
    harness.behaviors["dns_latency"] = lambda host: latency_source(
        host, dns_status=dns_status, resolved=False, dns_latency_ms=None, answer_count=0,
    )
    harness.behaviors["ipv_check"] = lambda host: addresses_source(host, ipv4_addresses=[], ipv6_addresses=[])

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.dns_query.status is S.COMPLETED and snapshot.dns_query.value.resolved is False
    assert snapshot.dns_latency.value.dns_status == dns_status
    assert snapshot.dns_addresses[0].value.ipv4_addresses == []


def test_empty_interface_and_socket_lists_are_completed(harness):
    harness.returns("interfaces", [])
    harness.returns("sockets", ListeningPortReport())

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.interfaces.status is S.COMPLETED and snapshot.interfaces.value == []
    assert snapshot.listening_sockets.status is S.COMPLETED and snapshot.listening_sockets.value == []


# --- invalid_result ---------------------------------------------------------------------


INVALID_OBSERVATIONS = [
    ("routing", lambda: RoutingSnapshot(hostname="host\x1b[31m")),
    ("routing", lambda: RoutingSnapshot(gateway="not-an-ip")),
    ("routing", lambda: RoutingSnapshot(hostname="host", gateway="192.168.1.1", errors={"gateway": "x"})),
    ("routing", lambda: RoutingSnapshot(hostname="host", errors={"unknown": "x"})),
    ("routing", lambda: RoutingSnapshot(hostname="host", routes=("a",))),
    ("routing", lambda: RoutingSnapshot(hostname="host", errors=None)),
    ("routing", lambda: {"hostname": "host"}),
    ("routing", lambda: None),
    ("interfaces", lambda: [NetworkInterface(connection="Wi\x00Fi")]),
    ("interfaces", lambda: [NetworkInterface(), "eth0"]),
    ("interfaces", lambda: (NetworkInterface(),)),
    ("interfaces", lambda: None),
    ("sockets", lambda: ListeningPortReport(sockets=[ListeningSocket(local_port="65536")])),
    ("sockets", lambda: ListeningPortReport(sockets=[ListeningSocket(peer_address="a b")])),
    ("sockets", lambda: ListeningPortReport(sockets=[None])),
    ("sockets", lambda: ListeningPortReport(sockets=())),
    ("sockets", lambda: [ListeningSocket()]),
    ("ping", lambda alias: None),
    ("ping", lambda alias: query_source(alias)),
    ("ping", lambda alias: ping_source("8.8.8.8")),
    ("ping", lambda alias: ping_source(alias, packet_loss_percent=150.0)),
    ("ping", lambda alias: ping_source(alias, avg_rtt_ms=float("nan"))),
    ("ping", lambda alias: ping_source(alias, packets_received=9)),
    ("ping", lambda alias: ping_source(alias, execution_status="completed")),
    ("ping", lambda alias: ping_source(alias, execution_status=None)),
    ("dns_query", lambda host: query_source(host, ip_address="bogus")),
    ("dns_query", lambda host: query_source("other.example")),
    ("dns_query", lambda host: latency_source(host)),
    ("dns_latency", lambda host: latency_source(host, answer_count=70000)),
    ("dns_latency", lambda host: latency_source(host, dns_status="no error")),
    ("dns_latency", lambda host: latency_source(host, dns_latency_ms=-1.0)),
    ("ipv_check", lambda host: addresses_source(host, ipv4_addresses=["2001:db8::1"])),
    ("ipv_check", lambda host: addresses_source(host, ipv4_addresses=("1.1.1.1",))),
    ("ipv_check", lambda host: addresses_source(host, ipv6_addresses=None)),
    ("ipv_check", lambda host: addresses_source("other.example")),
]
ACCESSORS = dict(OPERATIONS)


@pytest.mark.parametrize(("name", "build"), INVALID_OBSERVATIONS, ids=range(len(INVALID_OBSERVATIONS)))
def test_invalid_observation_becomes_invalid_result_with_timing(harness, name, build):
    harness.behaviors[name] = build

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    invalid = [r for r in sections(snapshot) if r.status is S.FAILED]
    assert harness.calls == DEFAULT_CALLS
    assert invalid and all(issue_pairs(r) == [("invalid_result", None)] for r in invalid)
    assert all(r.value is None and r.duration_seconds == MONOTONIC_STEP for r in invalid)
    assert len(invalid) == len([c for c in DEFAULT_CALLS if c[0] == name])
    assert all(r.status is S.COMPLETED for r in sections(snapshot) if r not in invalid)


# --- Non-Linux platforms -----------------------------------------------------------------


def test_non_linux_marks_requested_operations_unsupported_and_runs_nothing(harness, subprocess_run):
    harness.linux = False

    snapshot = diagnostic_service.collect_diagnostic_snapshot(include_interfaces=False, include_dns_latency=False)

    assert harness.calls == []
    subprocess_run.assert_not_called()
    for result in sections(snapshot):
        if result.operation_id in ("interfaces", "dns.latency"):
            assert_not_performed(result, S.NOT_COLLECTED, "not_requested")
        else:
            assert_not_performed(result, S.UNSUPPORTED, "unsupported_platform")
    assert len(snapshot.pings) == 4 and len(snapshot.dns_addresses) == 4
    assert snapshot.started_at == BASE_TIME and snapshot.duration_seconds == MONOTONIC_STEP


@pytest.mark.parametrize(("platform", "expected"), [
    ("linux", True), ("linux2", True), ("darwin", False), ("win32", False), ("freebsd14", False), ("", False),
])
def test_is_linux_follows_sys_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(diagnostic_service.sys, "platform", platform)

    assert diagnostic_service._is_linux() is expected


# --- Clocks, identifiers and timing --------------------------------------------------------


def test_every_attempted_operation_has_its_own_timing(harness):
    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    results = sections(snapshot)
    # Wall ticks: snapshot start, then two per operation, then snapshot end.
    for index, result in enumerate(results):
        assert result.started_at == BASE_TIME + WALL_STEP * (1 + 2 * index)
        assert result.finished_at == BASE_TIME + WALL_STEP * (2 + 2 * index)
        assert result.duration_seconds == MONOTONIC_STEP
    assert snapshot.started_at == BASE_TIME
    assert snapshot.finished_at == BASE_TIME + WALL_STEP * (1 + 2 * len(results))
    assert snapshot.duration_seconds == (1 + 2 * len(results)) * MONOTONIC_STEP
    assert all(r.started_at.tzinfo is timezone.utc for r in results)


def test_duration_comes_from_the_monotonic_clock_when_the_wall_clock_goes_backwards(harness):
    harness.wall_direction = -1

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    assert snapshot.finished_at < snapshot.started_at
    assert snapshot.duration_seconds > 0
    assert all(r.finished_at < r.started_at and r.duration_seconds == MONOTONIC_STEP for r in sections(snapshot))


def test_snapshot_id_is_requested_once_per_collection(monkeypatch, harness):
    ids = iter(["3f2b8c1e-9d4a-4b6e-8f10-2a7c5d9e1b34", "7a1d2c3b-4e5f-4a6b-9c8d-0e1f2a3b4c5d"])
    monkeypatch.setattr(diagnostic_service, "_new_snapshot_id", lambda: next(ids))

    first = diagnostic_service.collect_diagnostic_snapshot()
    second = diagnostic_service.collect_diagnostic_snapshot()

    assert first.snapshot_id != second.snapshot_id


def test_real_helpers_return_random_uuid_utc_time_and_a_monotonic_float():
    first = diagnostic_service._new_snapshot_id()
    second = diagnostic_service._new_snapshot_id()
    before = diagnostic_service._monotonic()
    after = diagnostic_service._monotonic()
    now = diagnostic_service._utc_now()

    assert first != second
    assert uuid.UUID(first).version == 4 and str(uuid.UUID(first)) == first
    assert isinstance(before, float) and after >= before
    assert now.tzinfo is timezone.utc


def test_collection_with_real_clocks_and_ids_builds_a_valid_snapshot(monkeypatch, harness):
    for name, function in REAL_HELPERS.items():
        monkeypatch.setattr(diagnostic_service, name, function)

    snapshot = diagnostic_service.collect_diagnostic_snapshot(ping_targets=("gateway",), address_hostnames=())

    snapshot.validate()
    assert snapshot.duration_seconds >= 0
    assert uuid.UUID(snapshot.snapshot_id).version == 4


# --- Isolation ------------------------------------------------------------------------------


def test_snapshot_values_are_detached_from_the_source_objects(harness):
    sources = {"routing": routing_source(), "interfaces": interfaces_source(), "sockets": sockets_source(),
               "ping": {}, "address": {}}
    harness.returns("routing", sources["routing"])
    harness.returns("interfaces", sources["interfaces"])
    harness.returns("sockets", sources["sockets"])
    harness.behaviors["ping"] = lambda alias: sources["ping"].setdefault(alias, ping_source(alias))
    harness.behaviors["ipv_check"] = lambda host: sources["address"].setdefault(host, addresses_source(host))

    snapshot = diagnostic_service.collect_diagnostic_snapshot()
    expected = diagnostic_service.collect_diagnostic_snapshot()

    sources["routing"].routes.append("tampered")
    sources["routing"].hostname = "tampered"
    sources["interfaces"][0].device = "tampered"
    sources["interfaces"].append(NetworkInterface())
    sources["sockets"].sockets[0].local_port = "9"
    sources["sockets"].sockets.clear()
    sources["ping"]["gateway"].avg_rtt_ms = 999.0
    sources["address"]["yle.fi"].ipv4_addresses.append("6.6.6.6")
    sources["address"]["yle.fi"].ipv6_addresses.clear()

    assert snapshot.routing.value == expected.routing.value
    assert snapshot.routing.value.routes == ["default via 192.168.1.1 dev eth0"]
    assert snapshot.interfaces.value == interfaces_source()
    assert snapshot.listening_sockets.value == sockets_source().sockets
    assert snapshot.pings[1].value.avg_rtt_ms == 2.0
    assert snapshot.dns_addresses[3].value == addresses_source("yle.fi")
    snapshot.validate()


def test_two_snapshots_share_no_mutable_objects(harness):
    legacy = {"routing": routing_source(), "interfaces": interfaces_source(), "sockets": sockets_source()}
    for name, value in legacy.items():
        harness.returns(name, value)

    first = diagnostic_service.collect_diagnostic_snapshot()
    second = diagnostic_service.collect_diagnostic_snapshot()

    assert first is not second and first.routing is not second.routing
    assert first.routing.value is not second.routing.value
    assert first.routing.value.routes is not second.routing.value.routes
    assert first.interfaces.value is not second.interfaces.value
    assert first.interfaces.value[0] is not second.interfaces.value[0]
    assert first.listening_sockets.value[0] is not second.listening_sockets.value[0]
    assert first.dns_addresses[0].value.ipv4_addresses is not second.dns_addresses[0].value.ipv4_addresses
    first.interfaces.value.clear()
    assert len(second.interfaces.value) == 2


def test_error_text_raw_output_and_analysis_are_not_copied(harness):
    harness.behaviors["ping"] = lambda alias: ping_source(
        alias, error="boom", raw_output="64 bytes \x1b[31m", latency_analysis="OK", rtt_range_analysis="Good",
    )
    harness.behaviors["dns_query"] = lambda host: query_source(host, error="getent says no")
    harness.behaviors["dns_latency"] = lambda host: latency_source(
        host, error="dig says no", latency_analysis=LatencyAnalysis(latency_ms=12.5, status="OK"),
    )
    harness.behaviors["ipv_check"] = lambda host: addresses_source(host, error="resolver text")

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    ping = snapshot.pings[0].value
    assert (ping.error, ping.raw_output, ping.latency_analysis, ping.rtt_range_analysis) == (None, "", None, None)
    assert snapshot.dns_query.value.error is None
    assert snapshot.dns_latency.value.error is None and snapshot.dns_latency.value.latency_analysis is None
    assert snapshot.dns_addresses[0].value.error is None
    assert all(r.status is S.COMPLETED for r in sections(snapshot))


def test_collection_prints_nothing_and_starts_no_process(harness, subprocess_run, system_command, capsys):
    diagnostic_service.collect_diagnostic_snapshot()

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    subprocess_run.assert_not_called()
    system_command.assert_not_called()


@pytest.mark.parametrize("hostname", ["bad host", "-x", "a/b", "exämple.com", "a" * 255, "a\n"])
def test_structure_check_rejects_hostnames_even_if_host_validation_is_bypassed(harness, monkeypatch, hostname):
    monkeypatch.setattr(diagnostic_service, "validate_host", lambda value: None)

    with pytest.raises(ValidationError):
        diagnostic_service.collect_diagnostic_snapshot(dns_hostname=hostname)
    with pytest.raises(ValidationError):
        diagnostic_service.collect_diagnostic_snapshot(address_hostnames=(hostname,))

    assert harness.calls == []


def test_ping_count_above_the_json_safe_range_is_invalid_result_and_snapshot_still_exports(harness):
    harness.behaviors["ping"] = lambda alias: ping_source(
        alias, packets_sent=2**53 if alias == "gateway" else 4, packets_received=0 if alias == "gateway" else 4,
    )

    snapshot = diagnostic_service.collect_diagnostic_snapshot()

    gateway = snapshot.pings[1]
    assert gateway.status is S.FAILED and gateway.value is None
    assert issue_pairs(gateway) == [("invalid_result", None)]
    assert snapshot.pings[0].status is S.COMPLETED
    data = snapshot_to_dict(snapshot)
    assert json.loads(json.dumps(data, allow_nan=False)) == data
