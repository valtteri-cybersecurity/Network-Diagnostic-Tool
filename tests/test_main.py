import builtins
from dataclasses import replace
import runpy
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

import main
from models import (
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    LatencyAnalysis, ListeningPortReport, ListeningSocket, NetworkInterface, PingResult,
    RoutingSnapshot, ServiceInfo, SocketCounts,
)
from exceptions import (
    CommandError, CommandNotFoundError, CommandTimeoutError, CommandExecutionError, DiagnosticError,
    ParsingError, ValidationError,
)


@pytest.fixture
def user_input(monkeypatch):
    # Exhausting a finite side_effect list raises StopIteration immediately.
    read_input = Mock(side_effect=AssertionError("No simulated input configured"))
    monkeypatch.setattr(main, "input", read_input, raising=False)
    monkeypatch.setattr(main.output, "clear_screen", Mock())
    monkeypatch.setattr(main.output, "mainmenu1", Mock())
    monkeypatch.setattr(main.diagnostic_service, "get_connection_status", Mock(return_value=True))
    return read_input


@pytest.mark.parametrize("text, maximum, expected", [
    ("1", 5, 1), ("5", 5, 5), ("3", 3, 3), (" 2 ", 5, 2),
])
def test_read_menu_choice_accepts_valid_input(text, maximum, expected, user_input, capsys):
    user_input.side_effect = [text]

    result = main.read_menu_choice(maximum)

    captured = capsys.readouterr()
    assert result == expected
    assert f"Choose action 1-{maximum}:" in captured.out
    assert "Invalid selection" not in captured.out
    user_input.assert_called_once_with()


@pytest.mark.parametrize("invalid", ["0", "-1", "6", "hello", "", "2.5"])
def test_read_menu_choice_retries_invalid_input(invalid, user_input, capsys):
    user_input.side_effect = [invalid, "2"]

    result = main.read_menu_choice(5)

    captured = capsys.readouterr()
    assert result == 2
    assert user_input.call_count == 2
    assert captured.out.count("Choose action 1-5:") == 2
    assert captured.out.count("Invalid selection. Enter a number from 1 to 5.") == 1


def test_read_menu_choice_retries_multiple_invalid_inputs(user_input, capsys):
    user_input.side_effect = ["text", "0", "6", "-1", "", "4"]

    result = main.read_menu_choice(5)

    captured = capsys.readouterr()
    assert result == 4
    assert user_input.call_count == 6
    assert captured.out.count("Choose action 1-5:") == 6
    assert captured.out.count("Invalid selection") == 5


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_read_menu_choice_propagates_input_interruption(error, user_input):
    user_input.side_effect = error

    with pytest.raises(type(error)):
        main.read_menu_choice(5)


def test_main_exits_from_main_menu(user_input):
    user_input.side_effect = ["5"]

    result = main.main()

    assert result is None
    user_input.assert_called_once_with()
    main.diagnostic_service.get_connection_status.assert_called_once_with()
    main.output.mainmenu1.assert_called_once_with(True)
    assert main.output.clear_screen.call_count == 2


@pytest.mark.parametrize("menu, back, menu_function", [
    ("1", "5", "mainmenu2"),
    ("2", "4", "mainmenu3"),
    ("3", "3", "mainmenu4"),
    ("4", "3", "mainmenu5"),
])
def test_main_returns_from_each_submenu(menu, back, menu_function, user_input, monkeypatch):
    user_input.side_effect = [menu, back, "5"]
    submenu = Mock()
    monkeypatch.setattr(main.output, menu_function, submenu)

    main.main()

    assert user_input.call_count == 3
    submenu.assert_called_once_with()
    assert main.output.mainmenu1.call_args_list == [call(True), call(True)]
    assert main.output.clear_screen.call_count == 3


def test_main_can_visit_all_submenus_in_one_session(user_input):
    user_input.side_effect = ["1", "5", "2", "4", "3", "3", "4", "3", "5"]

    main.main()

    assert user_input.call_count == 9
    assert main.output.mainmenu1.call_count == 5
    assert main.diagnostic_service.get_connection_status.call_count == 5


@pytest.mark.parametrize("choice, target", [
    ("1", "localhost"), ("2", "gateway"),
    ("3", "internet"), ("4", "google.com"),
])
def test_main_starts_selected_ping_diagnostic(choice, target, user_input, monkeypatch):
    user_input.side_effect = ["1", choice, "5", "5"]
    report = PingResult(target=target)
    diagnostic = Mock(return_value=report)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "run_ping_diagnostic", diagnostic)
    monkeypatch.setattr(main.output, "display_ping_report", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with(target)
    display.assert_called_once_with(report)
    assert display.call_args.args[0] is report


def test_main_starts_hostname_resolution(user_input, monkeypatch):
    user_input.side_effect = ["2", "1", "4", "5"]
    report = DnsQueryResult(
        resolved=True,
        hostname="google.com",
        execution_status=ExecutionStatus.COMPLETED,
        ip_address="192.0.2.10",
    )
    diagnostic = Mock(return_value=report)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "run_dns_query", diagnostic)
    monkeypatch.setattr(main.output, "dns_query_output", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with()
    display.assert_called_once_with(report)


# Classifying only measured latencies is tested in test_diagnostic_service.py.
@pytest.mark.parametrize("latency, latency_analysis", [
    (None, None), (12.0, LatencyAnalysis(latency_ms=12.0, status="EXCELLENT")),
])
def test_main_displays_dns_latency_with_service_analysis(
    latency, latency_analysis, user_input, monkeypatch,
):
    user_input.side_effect = ["2", "2", "4", "5"]
    report = DnsLatencyResult(
        dns_latency_ms=latency,
        dns_status="NOERROR",
        hostname="google.com",
        execution_status=ExecutionStatus.COMPLETED,
        latency_analysis=latency_analysis,
    )
    original_report = replace(report)
    diagnostic = Mock(return_value=report)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "run_dns_latency_test", diagnostic)
    monkeypatch.setattr(main.output, "dns_latency_output", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with()
    assert report == original_report
    display.assert_called_once_with(report, latency_analysis)


def test_main_displays_address_lookups_of_all_sites(user_input, monkeypatch):
    user_input.side_effect = ["2", "3", "4", "5"]
    sites = ["google.com", "wikipedia.com", "example.com", "yle.fi"]
    reports = [DnsAddressResult(
        hostname=site,
        execution_status=ExecutionStatus.COMPLETED,
        ipv4_addresses=["192.0.2.10"],
        ipv6_addresses=[],
    ) for site in sites]
    diagnostic = Mock(return_value=reports)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "lookup_ip_addresses", diagnostic)
    monkeypatch.setattr(main.output, "ipv_check_results_output", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with()
    display.assert_called_once_with(reports)


def test_main_displays_other_address_lookups_when_one_fails(user_input, subprocess_run, capsys):
    user_input.side_effect = ["2", "3", "4", "5"]

    def respond(arguments, **kwargs):
        if arguments[2] == "wikipedia.com":
            raise subprocess.TimeoutExpired(arguments, 10)
        if arguments[1] == "ahostsv4":
            return subprocess.CompletedProcess([], 0, f"192.0.2.10 STREAM {arguments[2]}\n", "")
        return subprocess.CompletedProcess([], 2, "", "")

    subprocess_run.side_effect = respond

    main.main()

    text = capsys.readouterr().out
    failed = text[text.index("Hostname : wikipedia.com"):text.index("Hostname : example.com")]
    assert "Execution: timeout" in failed
    assert "IPv4 addresses: Unavailable" in failed
    assert "Error    : getent timed out after 10 seconds." in failed
    assert text.count("  - 192.0.2.10") == 3
    assert "Hostname : yle.fi" in text
    assert user_input.call_count == 4
    assert main.output.mainmenu1.call_count == 2


def test_main_displays_listening_socket_counts_and_rows(user_input, monkeypatch):
    user_input.side_effect = ["3", "1", "3", "5"]
    ports = [ListeningSocket(protocol="TCP", state="LISTEN", local_port="22")]
    counts = SocketCounts(tcp_count=1, udp_count=0)
    report = ListeningPortReport(sockets=ports, counts=counts, services=[])
    collect = Mock(return_value=report)
    header = Mock()
    display_counts = Mock()
    display_ports = Mock()
    monkeypatch.setattr(main.diagnostic_service, "get_listening_ports", collect)
    monkeypatch.setattr(main.output, "display_open_ports_header", header)
    monkeypatch.setattr(main.output, "display_open_ports_count", display_counts)
    monkeypatch.setattr(main.output, "display_open_ports", display_ports)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    header.assert_called_once_with()
    display_counts.assert_called_once_with(counts)
    display_ports.assert_called_once_with(ports)


def test_main_displays_port_service_suggestions(user_input, monkeypatch):
    user_input.side_effect = ["3", "2", "3", "5"]
    services = [ServiceInfo(protocol="TCP", local_address="0.0.0.0", port=22, service_name="SSH")]
    report = ListeningPortReport(sockets=[ListeningSocket(protocol="TCP")], services=services)
    collect = Mock(return_value=report)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "get_listening_ports", collect)
    monkeypatch.setattr(main.output, "display_known_services", display)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    display.assert_called_once_with(services)


def test_main_integrates_socket_listing_and_service_suggestions(user_input, subprocess_run, capsys):
    user_input.side_effect = ["3", "1", "2", "3", "5"]
    raw = "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nudp UNCONN 0 0 0.0.0.0:68 0.0.0.0:*\n"
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    main.main()

    text = capsys.readouterr().out
    assert "Total 1 listening TCP sockets" in text
    assert "Total 1 unconnected UDP sockets" in text
    assert "SSH" in text
    assert "DHCP client" in text
    assert subprocess_run.call_count == 2


def test_main_displays_system_routing_snapshot(user_input, monkeypatch):
    user_input.side_effect = ["4", "1", "3", "5"]
    routes = ["default via 192.0.2.1 dev enp3s0 src 192.0.2.10"]
    snapshot = RoutingSnapshot(
        hostname="workstation", interface="enp3s0", ip_address="192.0.2.10",
        gateway="192.0.2.1", routes=routes,
    )
    collect = Mock(return_value=snapshot)
    display = Mock()
    display_error = Mock()
    monkeypatch.setattr(main.diagnostic_service, "get_routing_snapshot", collect)
    monkeypatch.setattr(main.output, "display_routing_table", display)
    monkeypatch.setattr(main.output, "display_command_error", display_error)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    display.assert_called_once_with("workstation", "enp3s0", "192.0.2.10", "192.0.2.1", routes)
    display_error.assert_not_called()


def test_main_displays_partial_routing_snapshot_and_each_error_once(user_input, monkeypatch):
    user_input.side_effect = ["4", "1", "3", "5"]
    message = "Invalid default route interface: Invalid interface name: '-f'"
    snapshot = RoutingSnapshot(
        hostname=None, gateway="192.0.2.1", routes=["default via 192.0.2.1 dev -f"],
        errors={"hostname": "hostname failed", "interface": message, "ip_address": message},
    )
    calls = Mock()
    monkeypatch.setattr(main.diagnostic_service, "get_routing_snapshot", Mock(return_value=snapshot))
    monkeypatch.setattr(main.output, "display_routing_table", calls.table)
    monkeypatch.setattr(main.output, "display_command_error", calls.error)

    main.main()

    assert calls.mock_calls == [
        call.table(None, None, None, "192.0.2.1", ["default via 192.0.2.1 dev -f"]),
        call.error("hostname failed"),
        call.error(message),
    ]
    assert user_input.call_count == 4
    assert main.output.mainmenu1.call_count == 2


def test_main_displays_networkmanager_interfaces(user_input, monkeypatch):
    user_input.side_effect = ["4", "2", "3", "5"]
    raw = ["enp3s0:ethernet:connected:Wired connection 1"]
    interfaces = [NetworkInterface(
        device="enp3s0",
        interface_type="ethernet",
        state="connected",
        connection="Wired connection 1",
    )]
    collect = Mock(return_value=raw)
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service.network, "nmcli_status", collect)
    monkeypatch.setattr(main.output, "display_network_interface_status", display)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    display.assert_called_once_with(interfaces)


@pytest.mark.parametrize("error_type", [
    CommandNotFoundError, CommandTimeoutError, CommandExecutionError, ValidationError, ParsingError,
])
def test_main_recovers_from_networkmanager_error(error_type, user_input, monkeypatch):
    user_input.side_effect = ["1", "5", "5"]
    status = Mock(side_effect=[error_type("nmcli unavailable"), False])
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, "get_connection_status", status)
    monkeypatch.setattr(main.output, "display_command_error", display)

    main.main()

    assert user_input.call_count == 3
    display.assert_called_once_with("nmcli unavailable")
    assert main.output.mainmenu1.call_args_list == [call(None), call(False)]


@pytest.mark.parametrize("menu, action, function_name", [
    ("1", "1", "run_ping_diagnostic"),
    ("2", "1", "run_dns_query"),
    ("2", "2", "run_dns_latency_test"),
    ("2", "3", "lookup_ip_addresses"),
    ("3", "1", "get_listening_ports"),
    ("3", "2", "get_listening_ports"),
    ("4", "1", "get_routing_snapshot"),
    ("4", "2", "get_network_interfaces"),
])
@pytest.mark.parametrize("error_type", [
    CommandError, CommandNotFoundError, CommandTimeoutError, CommandExecutionError, ValidationError,
    ParsingError, DiagnosticError,
])
def test_main_returns_to_main_menu_after_diagnostic_error(
    menu, action, function_name, error_type, user_input, monkeypatch,
):
    user_input.side_effect = [menu, action, "5"]
    diagnostic = Mock(side_effect=error_type("diagnostic failed"))
    display = Mock()
    monkeypatch.setattr(main.diagnostic_service, function_name, diagnostic)
    monkeypatch.setattr(main.output, "display_command_error", display)

    main.main()

    assert user_input.call_count == 3
    diagnostic.assert_called_once()
    display.assert_called_once_with("diagnostic failed")
    assert main.output.mainmenu1.call_count == 2


@pytest.mark.parametrize("inputs", [["5"], [KeyboardInterrupt()], [EOFError()]])
def test_script_entry_displays_exit_message_after_exit_or_interruption(
    inputs, user_input, monkeypatch,
):
    read_input = Mock(side_effect=inputs)
    display_exit = Mock()
    monkeypatch.setattr(builtins, "input", read_input)
    monkeypatch.setattr(main.output, "display_exit_message", display_exit)

    runpy.run_path(str(Path(main.__file__)), run_name="__main__")

    read_input.assert_called_once_with()
    display_exit.assert_called_once_with()
    user_input.assert_not_called()


def test_main_displays_snapshot_without_default_route(user_input, subprocess_run, monkeypatch):
    user_input.side_effect = ["4", "1", "3", "5"]
    responses = {
        "ip": subprocess.CompletedProcess([], 0, "", ""),
        "hostname": subprocess.CompletedProcess([], 0, "workstation\n", ""),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]
    display = Mock()
    display_error = Mock()
    monkeypatch.setattr(main.output, "display_routing_table", display)
    monkeypatch.setattr(main.output, "display_command_error", display_error)

    main.main()

    assert user_input.call_count == 4
    display.assert_called_once_with("workstation", None, None, None, [])
    display_error.assert_not_called()


@pytest.mark.parametrize("error, message", [
    (FileNotFoundError("missing"), "Required command not found: ip"),
    (subprocess.TimeoutExpired(["ip", "-4", "route"], 10), "ip timed out after 10 seconds."),
    (OSError("Permission denied"), "Could not execute ip: Permission denied"),
])
def test_main_recovers_from_real_runner_errors(error, message, user_input, subprocess_run, monkeypatch):
    # The hostname does not depend on the routing table and is still displayed.
    user_input.side_effect = ["4", "1", "3", "5"]

    def respond(arguments, **kwargs):
        if arguments[0] == "ip":
            raise error
        return subprocess.CompletedProcess([], 0, "workstation\n", "")

    subprocess_run.side_effect = respond
    display = Mock()
    display_table = Mock()
    monkeypatch.setattr(main.output, "display_command_error", display)
    monkeypatch.setattr(main.output, "display_routing_table", display_table)

    main.main()

    assert user_input.call_count == 4
    assert main.output.mainmenu1.call_count == 2
    display.assert_called_once_with(message)
    display_table.assert_called_once_with("workstation", None, None, None, None)
    assert [item.args[0] for item in subprocess_run.call_args_list] == [["ip", "-4", "route"], ["hostname"]]


@pytest.mark.parametrize("fixture_name, returncode, received, loss, latency, variation", [
    ("success.txt", 0, "3", "0%", "OK", "Good"),
    ("partial_loss.txt", 0, "2", "33.3333%", "OK", "Good"),
    ("total_loss.txt", 1, "0", "100%", "Not available", "Not available"),
])
def test_main_integrates_ping_measurements_analysis_and_report(
    fixture_name, returncode, received, loss, latency, variation, user_input, subprocess_run, capsys,
):
    user_input.side_effect = ["1", "3", "5", "5"]
    raw = (Path(__file__).parent / "fixtures" / "ping" / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], returncode, raw, "")

    main.main()

    text = capsys.readouterr().out
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    assert text.count("INTERNET PING REPORT") == 1
    assert raw in text
    assert fields["status"] == "completed"
    assert fields["sent"] == "3"
    assert fields["received"] == received
    assert fields["loss"] == loss
    assert fields["Latency analysis"] == latency
    assert fields["RTT range analysis"] == variation
    if received == "0":
        assert fields["reachable"] == "False"
        # The fixture reports "+3 errors" (destination unreachable).
        assert "No replies were received, but 3 ICMP error messages" in fields["reason"]
    else:
        assert fields["reachable"] == "True"
        assert "packets were" in fields["reason"]
    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "3", "-W", "2", "8.8.8.8"]
    subprocess_run.assert_called_once()
    assert user_input.call_count == 4


def test_main_reports_ping_technical_failure_instead_of_stdout_statistics(user_input, subprocess_run, capsys):
    user_input.side_effect = ["1", "3", "5", "5"]
    raw = (Path(__file__).parent / "fixtures" / "ping" / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], 2, raw, "ping: sendmsg: Operation not permitted\n",
    )

    main.main()

    text = capsys.readouterr().out
    fields = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    assert fields["status"] == "failed"
    assert fields["error"] == "ping failed: ping: sendmsg: Operation not permitted"
    assert fields["reachable"] == "None"
    assert fields["Latency analysis"] == "Not available"
    assert fields["RTT range analysis"] == "Not available"
    assert "PING_ERROR:" not in text
    assert user_input.call_count == 4


@pytest.mark.parametrize("error, status, message, raw", [
    (subprocess.TimeoutExpired(["ping"], 10, output=b"partial reply\n"),
     "timeout", "ping timed out after 10 seconds.", "partial reply"),
    (FileNotFoundError("missing"), "failed", "Required command not found: ping", ""),
])
def test_main_reports_ping_execution_errors_and_allows_continuing(
    error, status, message, raw, user_input, subprocess_run, capsys,
):
    user_input.side_effect = ["1", "3", "1", "5", "5"]
    success = (Path(__file__).parent / "fixtures" / "ping" / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = [error, subprocess.CompletedProcess([], 0, success, "")]

    main.main()

    captured = capsys.readouterr()
    first_report, second_report = captured.out.split("LOCALHOST PING REPORT")
    assert "INTERNET PING REPORT" in first_report
    assert f"Error: {message}" in first_report
    assert f"{'status':<20}: {status}" in first_report
    assert f"{'reachable':<20}: None" in first_report
    assert f"{'Latency analysis':<20}: Not available" in first_report
    assert f"{'RTT range analysis':<20}: Not available" in first_report
    if raw:
        assert raw in first_report
    assert f"{'status':<20}: completed" in second_report
    assert f"{'reachable':<20}: True" in second_report
    assert "PING_ERROR:" not in captured.out
    assert captured.err == ""
    assert [item.args[0][-1] for item in subprocess_run.call_args_list] == ["8.8.8.8", "127.0.0.1"]
    assert user_input.call_count == 5


def test_main_displays_zero_rtt_analysis(user_input, subprocess_run, capsys):
    user_input.side_effect = ["1", "1", "5", "5"]
    raw = (
        "3 packets transmitted, 3 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 0.000/0.000/0.000/0.000 ms\n"
    )
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    main.main()

    text = capsys.readouterr().out
    assert f"{'status':<20}: completed" in text
    assert f"{'reachable':<20}: True" in text
    assert f"{'avg':<20}: 0.0 ms" in text
    assert f"{'mdev':<20}: 0.0 ms" in text
    assert f"{'Latency analysis':<20}: Excellent" in text
    assert f"{'RTT range analysis':<20}: Excellent" in text


def test_main_can_run_another_diagnostic_after_raised_command_error(user_input, monkeypatch):
    user_input.side_effect = ["2", "1", "1", "1", "5", "5"]
    report = PingResult(target="localhost")
    query = Mock(side_effect=CommandError("getent unavailable"))
    ping_diagnostic = Mock(return_value=report)
    display_error = Mock()
    display_report = Mock()
    monkeypatch.setattr(main.diagnostic_service, "run_dns_query", query)
    monkeypatch.setattr(main.diagnostic_service, "run_ping_diagnostic", ping_diagnostic)
    monkeypatch.setattr(main.output, "display_command_error", display_error)
    monkeypatch.setattr(main.output, "display_ping_report", display_report)

    main.main()

    query.assert_called_once_with()
    display_error.assert_called_once_with("getent unavailable")
    ping_diagnostic.assert_called_once_with("localhost")
    display_report.assert_called_once_with(report)
    assert user_input.call_count == 6


def test_main_reports_invalid_route_interface_without_address_command(user_input, subprocess_run, capsys):
    user_input.side_effect = ["4", "1", "3", "5"]
    responses = {
        "ip": subprocess.CompletedProcess([], 0, "default via 192.0.2.1 dev -f\n", ""),
        "hostname": subprocess.CompletedProcess([], 0, "workstation\n", ""),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]

    main.main()

    output = capsys.readouterr().out
    assert output.count("Error: Invalid default route interface: Invalid interface name: '-f'") == 1
    # The hostname and gateway do not depend on the invalid interface name.
    assert "Hostname : workstation" in output
    assert "Gateway  : 192.0.2.1" in output
    assert "Interface: Unavailable" in output
    assert user_input.call_count == 4
    assert main.output.mainmenu1.call_count == 2
    assert [item.args[0] for item in subprocess_run.call_args_list] == [
        ["ip", "-4", "route"], ["hostname"],
    ]


def test_main_reports_invalid_socket_line_instead_of_partial_service_list(
    user_input, subprocess_run, monkeypatch, capsys,
):
    # The error returns the CLI to the main menu, as for other DiagnosticErrors.
    user_input.side_effect = ["3", "2", "5"]
    raw = (
        "tcp LISTEN 0 128 0.0.0.0:99999 0.0.0.0:*\n"
        "tcp LISTEN 0 128 not-an-address:22 0.0.0.0:*\n"
        "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
    )
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")
    display = Mock()
    monkeypatch.setattr(main.output, "display_known_services", display)

    main.main()

    # Skipping the invalid lines would present port 22 as the complete list.
    display.assert_not_called()
    assert "Error: Unrecognized line in ss output" in capsys.readouterr().out


def test_main_reports_invalid_route_source_address_as_controlled_error(user_input, subprocess_run, capsys):
    user_input.side_effect = ["4", "1", "3", "5"]
    responses = {
        "ip": subprocess.CompletedProcess([], 0, "default via 192.0.2.1 dev enp3s0 src not-an-address\n", ""),
        "hostname": subprocess.CompletedProcess([], 0, "workstation\n", ""),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]

    main.main()

    output = capsys.readouterr().out
    assert "Error: Invalid default route source address: Invalid IPv4 address: 'not-an-address'" in output
    # The raw route line is part of the routing table; the parsed IP is not shown.
    assert "IP       : Unavailable" in output
    assert "Interface: enp3s0" in output
    assert user_input.call_count == 4
