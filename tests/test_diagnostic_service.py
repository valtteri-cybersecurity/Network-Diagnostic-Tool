import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from network_diagnostic import diagnostic_service
from network_diagnostic.core.exceptions import (
    CommandError, CommandExecutionError, CommandNotFoundError, CommandTimeoutError,
    DiagnosticError, ParsingError, ValidationError,
)
from network_diagnostic.core.models import (
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
