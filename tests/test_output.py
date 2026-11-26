from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

import pytest

import output
from models import (
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    LatencyAnalysis, ListeningSocket, NetworkInterface, PacketLossStatus, PingResult, ServiceInfo, SocketCounts,
)


def test_clear_screen_uses_clear_command(monkeypatch):
    command = Mock(return_value=0)
    monkeypatch.setattr(output.os, "system", command)

    result = output.clear_screen()

    assert result is None
    command.assert_called_once_with("clear")


def test_display_menu_formats_title_numbered_options_and_separators(capsys):
    output.display_menu("TEST MENU", ["First option", "Second option"])

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert "TEST MENU" in lines
    assert "    1. First option" in lines
    assert "    2. Second option" in lines
    assert lines.count("-" * 60) == 2
    assert captured.err == ""


def test_display_menu_handles_empty_options(capsys):
    output.display_menu("EMPTY MENU", [])

    text = capsys.readouterr().out
    assert "EMPTY MENU" in text
    assert "1." not in text
    assert text.count("-" * 60) == 2


@pytest.mark.parametrize("connected, expected_status", [
    (True, "Connected interface detected"),
    (False, "No connected interface detected"),
    (None, "NetworkManager status unavailable"),
])
def test_mainmenu1_displays_fixed_date_connection_state_and_options(
    connected, expected_status, monkeypatch, capsys,
):
    clock = Mock()
    clock.now.return_value = datetime(2026, 11, 25, 12, 0)
    monkeypatch.setattr(output, "datetime", clock)

    output.mainmenu1(connected)

    text = capsys.readouterr().out
    assert "25-11-2026" in text
    assert expected_status in text
    assert "NETWORK DIAGNOSTIC TOOL" in text
    for number, label in enumerate([
        "Ping diagnostics", "DNS diagnostics", "Port analysis", "System snapshot", "Exit program",
    ], start=1):
        assert f"{number}. {label}" in text
    clock.now.assert_called_once_with()


@pytest.mark.parametrize("function, title, labels", [
    (output.mainmenu2, "PING DIAGNOSTICS", [
        "Localhost ping", "Gateway ping", "Internet ping", "Internet DNS ping", "Back to main menu",
    ]),
    (output.mainmenu3, "DNS DIAGNOSTICS", [
        "Query test", "Latency test", "IPv4 / IPv6 address test", "Back to main menu",
    ]),
    (output.mainmenu4, "PORT ANALYSIS", [
        "List listening sockets", "Show common service names by port", "Back to main menu",
    ]),
    (output.mainmenu5, "SYSTEM SNAPSHOT", [
        "Routing table overview", "Network interface status", "Back to main menu",
    ]),
])
def test_submenus_display_expected_title_and_numbered_actions(function, title, labels, capsys):
    function()

    text = capsys.readouterr().out
    assert title in text
    for number, label in enumerate(labels, start=1):
        assert f"{number}. {label}" in text


@pytest.mark.parametrize("maximum", [1, 5])
def test_display_menu_prompt_has_correct_range_without_newline(maximum, capsys):
    output.display_menu_prompt(maximum)

    captured = capsys.readouterr()
    assert captured.out == f"Choose action 1-{maximum}: "
    assert captured.err == ""


def test_display_menu_error_explains_allowed_range(capsys):
    output.display_menu_error(4)

    text = capsys.readouterr().out
    assert "Invalid selection." in text
    assert "Enter a number from 1 to 4." in text


def test_display_command_error_includes_original_message(capsys):
    output.display_command_error("nmcli timed out after 10 seconds.")

    text = capsys.readouterr().out
    assert "Error:" in text
    assert "nmcli timed out after 10 seconds." in text


def test_display_exit_message_confirms_program_closed(capsys):
    output.display_exit_message()

    assert "Program closed." in capsys.readouterr().out


def test_dns_output_header_displays_title_and_matching_separator(capsys):
    output.dns_output_header("DNS TEST")

    lines = capsys.readouterr().out.splitlines()
    assert "DNS TEST" in lines
    assert "--------" in lines


@pytest.mark.parametrize("loss_status, loss, expected", [
    (PacketLossStatus.UNAVAILABLE, None, "Packet loss information is unavailable."),
    (PacketLossStatus.NO_LOSS, 0, "All test packets were received."),
    (PacketLossStatus.TOTAL_LOSS, 100,
     "No test packets were received. The target did not respond or ICMP traffic is blocked."),
    (PacketLossStatus.PARTIAL_LOSS, 33.3333,
     "33.3333% of test packets were lost. Packet loss may indicate a connection problem."),
    (PacketLossStatus.INVALID, -1, "Invalid packet loss value."),
    (PacketLossStatus.INVALID, 101, "Invalid packet loss value."),
])
def test_display_ping_report_formats_given_packet_loss_classification(loss_status, loss, expected, capsys):
    raw = "3 packets transmitted, see report for packet loss"
    report = PingResult(
        target="internet", packet_loss_percent=loss, raw_output=raw, packet_loss_status=loss_status,
    )

    result = output.display_ping_report(report)

    text = capsys.readouterr().out
    assert result is None
    assert expected in text
    assert raw in text


def test_display_ping_result_prioritizes_command_error_over_packet_loss(capsys):
    report = PingResult(
        target="internet", execution_status=ExecutionStatus.TIMEOUT,
        error="ping timed out after 10 seconds.", raw_output="partial reply", packet_loss_percent=0,
    )

    result = output.display_ping_result(report)

    text = capsys.readouterr().out
    assert result is None
    assert "partial reply" in text
    assert "Error: ping timed out after 10 seconds." in text
    assert "PING_ERROR:" not in text


def test_display_ping_report_formats_successful_measurement_units(capsys):
    report = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=3, packet_loss_percent=0.0,
        min_rtt_ms=10.0, avg_rtt_ms=12.5, max_rtt_ms=15.0, mdev_ms=2.041, reachable=True,
        packet_loss_status=PacketLossStatus.NO_LOSS, latency_analysis="OK", rtt_range_analysis="Good",
    )

    output.display_ping_report(report)

    text = capsys.readouterr().out
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    assert "INTERNET PING REPORT" in text
    assert fields == {
        "status": "completed", "reason": "All test packets were received.",
        "sent": "3", "received": "3", "loss": "0%", "min": "10.0 ms",
        "avg": "12.5 ms", "max": "15.0 ms", "mdev": "2.041 ms", "reachable": "True",
        "Latency analysis": "OK", "RTT range analysis": "Good",
    }


def test_display_ping_report_handles_failed_measurements_and_missing_values(capsys):
    report = PingResult(target="localhost", error="No packet statistics found")

    output.display_ping_report(report)

    text = capsys.readouterr().out
    assert "LOCALHOST PING REPORT" in text
    assert "failed" in text
    assert "No packet statistics found" in text
    assert text.count("None") == 8
    assert text.count("Not available") == 2
    assert " ms" not in text
    assert "%" not in text


def test_display_ping_report_handles_empty_report(capsys):
    output.display_ping_report(PingResult(target="gateway"))

    text = capsys.readouterr().out
    assert "GATEWAY PING REPORT" in text
    assert "Latency analysis" in text
    assert "RTT range analysis" in text
    assert text.count("Not available") == 2


@pytest.mark.parametrize("report, site, status, address", [
    (DnsQueryResult("example.com", ExecutionStatus.COMPLETED, True, "192.0.2.10"),
     "example.com", "OK", "192.0.2.10"),
    (DnsQueryResult("example.com", ExecutionStatus.COMPLETED, False),
     "example.com", "FAILED", "No address found"),
    (DnsQueryResult(), "Unknown", "UNKNOWN", "No address found"),
])
def test_dns_query_output_displays_resolution_or_missing_data(report, site, status, address, capsys):
    output.dns_query_output(report)

    text = capsys.readouterr().out
    assert "HOSTNAME RESOLUTION TEST" in text
    assert f"Hostname  : {site}" in text
    assert f"Status    : {status}" in text
    assert f"IP address: {address}" in text
    assert f"Execution : {report.execution_status.value}" in text


@pytest.mark.parametrize("analysis, description", [
    (LatencyAnalysis(12.345, "EXCELLENT"), "Excellent - very fast DNS response"),
    (LatencyAnalysis(12.345, "GOOD"), "Good - fast DNS response"),
    (LatencyAnalysis(12.345, "SLOW"), "Slow - DNS response may need investigation"),
    (LatencyAnalysis(12.345, "CUSTOM"), "CUSTOM"),
    (None, "UNKNOWN"),
])
def test_dns_latency_output_displays_measurement_and_classification(analysis, description, capsys):
    report = DnsLatencyResult(
        hostname="example.com", execution_status=ExecutionStatus.COMPLETED,
        dns_latency_ms=12.345, dns_status="NOERROR", resolved=True, answer_count=2,
    )

    output.dns_latency_output(report, analysis)

    text = capsys.readouterr().out
    assert "DNS LATENCY TEST" in text
    assert "example.com" in text
    assert "DNS status: NOERROR" in text
    assert "Resolved  : Yes" in text
    assert "Measurement completed" in text
    assert "12.35 ms" in text
    assert f"Analysis  : {description}" in text


def test_dns_latency_output_does_not_treat_fast_nxdomain_as_resolved(capsys):
    report = DnsLatencyResult(
        hostname="missing.example.com", execution_status=ExecutionStatus.COMPLETED,
        dns_latency_ms=0.0, dns_status="NXDOMAIN", resolved=False, answer_count=0,
    )

    output.dns_latency_output(report)

    text = capsys.readouterr().out
    assert "missing.example.com" in text
    assert "Execution : completed" in text
    assert "DNS status: NXDOMAIN" in text
    assert "Resolved  : No" in text
    assert "0.00 ms" in text
    assert "Analysis  : UNKNOWN" in text


@pytest.mark.parametrize("report, site, dns_status, resolution", [
    (DnsLatencyResult("example.com", ExecutionStatus.COMPLETED, "SERVFAIL", False),
     "example.com", "SERVFAIL", "No"),
    (DnsLatencyResult(), "Unknown", "UNKNOWN", "Unknown"),
])
def test_dns_latency_output_handles_missing_measurement(report, site, dns_status, resolution, capsys):
    output.dns_latency_output(report)

    text = capsys.readouterr().out
    assert f"Hostname  : {site}" in text
    assert f"DNS status: {dns_status}" in text
    assert f"Resolved  : {resolution}" in text
    assert "Measurement unavailable" in text
    assert "DNS response time could not be measured" in text
    assert "Measurement completed" not in text


@pytest.mark.parametrize("ipv4, ipv6", [
    (["192.0.2.10", "192.0.2.20"], ["2001:db8::10", "2001:db8::20"]),
    (["192.0.2.10"], []), ([], ["2001:db8::10"]), ([], []),
])
def test_ipv_check_output_lists_available_families_and_missing_addresses(ipv4, ipv6, capsys):
    report = DnsAddressResult("example.com", ExecutionStatus.COMPLETED, ipv4, ipv6)

    output.ipv_check_output(report)

    text = capsys.readouterr().out
    assert "Hostname : example.com" in text
    for address in ipv4 + ipv6:
        assert f"  - {address}" in text
    assert ("IPv4 addresses: None found" in text) == (not ipv4)
    assert ("IPv6 addresses: None found" in text) == (not ipv6)


def test_ipv_check_output_handles_missing_fields(capsys):
    output.ipv_check_output(DnsAddressResult())

    text = capsys.readouterr().out
    assert "Hostname : Unknown" in text
    assert "IPv4 addresses: None found" in text
    assert "IPv6 addresses: None found" in text


def test_ipv_check_results_output_prints_each_site_in_order(capsys):
    reports = [
        DnsAddressResult("first.example.com", ExecutionStatus.COMPLETED, ["192.0.2.10"], []),
        DnsAddressResult("second.example.com", ExecutionStatus.COMPLETED, [], ["2001:db8::20"]),
    ]

    output.ipv_check_results_output(reports)

    text = capsys.readouterr().out
    assert "RESOLVED IPv4 / IPv6 ADDRESSES" in text
    assert text.index("first.example.com") < text.index("second.example.com")
    assert "192.0.2.10" in text
    assert "2001:db8::20" in text


def test_ipv_check_results_output_handles_empty_results(capsys):
    output.ipv_check_results_output([])

    text = capsys.readouterr().out
    assert "RESOLVED IPv4 / IPv6 ADDRESSES" in text
    assert "Hostname" not in text


def test_display_open_ports_header_describes_socket_states(capsys):
    output.display_open_ports_header()

    assert "LISTENING TCP / UNCONNECTED UDP SOCKETS" in capsys.readouterr().out


@pytest.mark.parametrize("tcp, udp", [(2, 1), (0, 0)])
def test_display_open_ports_count_prints_both_protocol_counts(tcp, udp, capsys):
    output.display_open_ports_count(SocketCounts(tcp, udp))

    text = capsys.readouterr().out
    assert f"Total {tcp} listening TCP sockets" in text
    assert f"Total {udp} unconnected UDP sockets" in text


def test_display_open_ports_prints_ipv4_ipv6_and_missing_fields(capsys):
    rows = [
        ListeningSocket("TCP", "LISTEN", "0.0.0.0", "22"),
        ListeningSocket("UDP", "UNCONN", "[::]", "53"),
        ListeningSocket(),
    ]

    output.display_open_ports(rows)

    text = capsys.readouterr().out
    assert all(label in text for label in ("PROTOCOL", "STATE", "LOCAL ADDRESS", "PORT"))
    data_rows = [line.split() for line in text.splitlines()
                 if line.startswith(("TCP", "UDP", "UNKNOWN"))]
    assert data_rows == [
        ["TCP", "LISTEN", "0.0.0.0", "22"],
        ["UDP", "UNCONN", "[::]", "53"],
        ["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"],
    ]


def test_display_open_ports_handles_empty_rows(capsys):
    output.display_open_ports([])

    assert "No matching TCP or UDP sockets found." in capsys.readouterr().out


def test_display_known_services_prints_service_rows_and_missing_fields(capsys):
    rows = [
        ServiceInfo("TCP", "0.0.0.0", 22, "SSH"),
        ServiceInfo("UDP", "[::]", 53, "Domain Name System (DNS)"),
        ServiceInfo(),
    ]

    output.display_known_services(rows)

    text = capsys.readouterr().out
    assert "COMMON SERVICE NAMES BY PORT" in text
    assert all(label in text for label in ("PROTOCOL", "LOCAL ADDRESS", "PORT", "SERVICE"))
    data_rows = [line.split(maxsplit=3) for line in text.splitlines()
                 if line.startswith(("TCP", "UDP", "UNKNOWN"))]
    assert data_rows == [
        ["TCP", "0.0.0.0", "22", "SSH"],
        ["UDP", "[::]", "53", "Domain Name System (DNS)"],
        ["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"],
    ]


def test_display_known_services_handles_empty_rows(capsys):
    output.display_known_services([])

    text = capsys.readouterr().out
    assert "COMMON SERVICE NAMES BY PORT" in text
    assert "No matching TCP or UDP sockets found." in text


def test_display_routing_table_prints_snapshot_and_all_routes(capsys):
    routes = ["default via 192.0.2.1 dev enp3s0",
              "192.0.2.0/24 dev enp3s0 proto kernel scope link src 192.0.2.10"]

    output.display_routing_table("workstation", "enp3s0", "192.0.2.10", "192.0.2.1", routes)

    text = capsys.readouterr().out
    assert "ROUTING TABLE OVERVIEW" in text
    assert "Hostname : workstation" in text
    assert "Interface: enp3s0" in text
    assert "IP       : 192.0.2.10" in text
    assert "Gateway  : 192.0.2.1" in text
    assert "IPv4 routes:" in text
    for route in routes:
        assert route in text


@pytest.mark.parametrize("missing", [None, ""])
def test_display_routing_table_handles_missing_snapshot_and_routes(missing, capsys):
    output.display_routing_table(missing, missing, missing, missing, [])

    text = capsys.readouterr().out
    assert text.count("Unavailable") == 4
    assert "No route information available." in text


def test_display_network_interface_status_prints_each_interface(capsys):
    interfaces = [
        NetworkInterface("enp3s0", "ethernet", "connected", "Office: Wired"),
        NetworkInterface("wlp2s0", "wifi", "disconnected", ""),
        NetworkInterface(),
    ]

    output.display_network_interface_status(interfaces)

    text = capsys.readouterr().out
    assert "NETWORK INTERFACE STATUS" in text
    assert "Device    : enp3s0" in text
    assert "Type      : ethernet" in text
    assert "State     : connected" in text
    assert "Connection: Office: Wired" in text
    assert "Device    : wlp2s0" in text
    assert "Type      : wifi" in text
    assert "State     : disconnected" in text
    assert "Device    : UNKNOWN" in text
    assert "Type      : UNKNOWN" in text
    assert "State     : UNKNOWN" in text
    assert text.count("Connection: --") == 2


def test_display_network_interface_status_handles_empty_interfaces(capsys):
    output.display_network_interface_status([])

    text = capsys.readouterr().out
    assert "NETWORK INTERFACE STATUS" in text
    assert "No network interface information available." in text


@pytest.mark.parametrize("function, report, error", [
    (output.dns_query_output,
     DnsQueryResult(hostname="example.com", error="lookup failed"), "lookup failed"),
    (output.dns_latency_output,
     DnsLatencyResult(hostname="example.com", dns_latency_ms=12.0, error="invalid DNS response"),
     "invalid DNS response"),
    (output.ipv_check_output,
     DnsAddressResult(hostname="example.com", error="address lookup failed"), "address lookup failed"),
])
def test_dns_outputs_display_structured_errors(function, report, error, capsys):
    function(report)

    text = capsys.readouterr().out
    assert "example.com" in text
    assert "failed" in text
    assert error in text
    if isinstance(report, DnsLatencyResult):
        assert "Measurement failed" in text
        assert "Measurement completed" not in text
        assert "Resolved  : Unknown" in text
        assert "12.00 ms" in text


@pytest.mark.parametrize("function, arguments", [
    (output.display_ping_result, (PingResult(raw_output="reply", error="timeout"),)),
    (output.display_ping_report, (PingResult(
        target="internet", raw_output="reply", error="timeout", execution_status=ExecutionStatus.TIMEOUT,
    ),)),
    (output.dns_query_output, (DnsQueryResult(hostname="example.com", resolved=True, ip_address="192.0.2.10"),)),
    (output.dns_latency_output, (DnsLatencyResult(hostname="example.com", dns_latency_ms=12), LatencyAnalysis(12, "EXCELLENT"))),
    (output.ipv_check_output, (DnsAddressResult(hostname="example.com", ipv4_addresses=["192.0.2.10"], ipv6_addresses=["2001:db8::10"]),)),
    (output.ipv_check_results_output, ([DnsAddressResult(hostname="example.com", ipv4_addresses=["192.0.2.10"])],)),
    (output.display_open_ports_count, (SocketCounts(1, 2),)),
    (output.display_open_ports, ([ListeningSocket("TCP", "LISTEN", "0.0.0.0", "22")],)),
    (output.display_known_services, ([ServiceInfo("TCP", "0.0.0.0", 22, "SSH")],)),
    (output.display_network_interface_status, ([NetworkInterface("eth0", "ethernet", "connected", "Office")],)),
    (output.display_routing_table, ("host", "eth0", "192.0.2.10", "192.0.2.1", ["default via 192.0.2.1 dev eth0"])),
])
def test_reports_do_not_mutate_data_or_run_commands(function, arguments, subprocess_run, system_command, capsys):
    original = deepcopy(arguments)

    result = function(*arguments)

    assert result is None
    assert arguments == original
    assert capsys.readouterr().err == ""
    subprocess_run.assert_not_called()
    system_command.assert_not_called()


def test_display_ping_report_uses_supplied_analyses_without_reclassifying_measurements(capsys):
    # Deliberately inconsistent values prove that output only presents engine data.
    report = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=0, packet_loss_percent=100.0, reachable=True,
        min_rtt_ms=0.0, avg_rtt_ms=0.0, max_rtt_ms=0.0,
        packet_loss_status=PacketLossStatus.NO_LOSS, latency_analysis="Poor", rtt_range_analysis="Unstable",
    )
    original = deepcopy(report)

    output.display_ping_report(report)

    text = capsys.readouterr().out
    assert "All test packets were received." in text
    assert "No test packets were received." not in text
    assert f"{'Latency analysis':<20}: Poor" in text
    assert f"{'RTT range analysis':<20}: Unstable" in text
    assert f"{'reachable':<20}: True" in text
    assert f"{'loss':<20}: 100%" in text
    assert report == original


def test_display_ping_report_prioritizes_given_error_over_packet_loss_explanation(capsys):
    report = PingResult(
        error="Unable to parse latency statistics", packet_loss_percent=0.0,
        packet_loss_status=PacketLossStatus.NO_LOSS,
    )

    output.display_ping_report(report)

    text = capsys.readouterr().out
    assert f"{'reason':<20}: Unable to parse latency statistics" in text
    assert "All test packets were received." not in text
