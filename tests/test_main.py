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
    LatencyAnalysis, ListeningSocket, NetworkInterface, PingResult, ServiceInfo, SocketCounts,
)
from exceptions import (
    CommandError, CommandNotFoundError, CommandTimeoutError, CommandExecutionError, ValidationError,
)


@pytest.fixture
def user_input(monkeypatch):
    # Exhausting a finite side_effect list raises StopIteration immediately.
    read_input = Mock(side_effect=AssertionError("No simulated input configured"))
    monkeypatch.setattr(main, "input", read_input, raising=False)
    monkeypatch.setattr(main.output, "clear_screen", Mock())
    monkeypatch.setattr(main.output, "mainmenu1", Mock())
    monkeypatch.setattr(main.network, "get_network_plugin", Mock(return_value=True))
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
    main.network.get_network_plugin.assert_called_once_with()
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
    assert main.network.get_network_plugin.call_count == 5


@pytest.mark.parametrize("choice, target", [
    ("1", "localhost"), ("2", "gateway"),
    ("3", "internet"), ("4", "google.com"),
])
def test_main_starts_selected_ping_diagnostic(choice, target, user_input, monkeypatch):
    user_input.side_effect = ["1", choice, "5", "5"]
    report = PingResult(target=target)
    diagnostic = Mock(return_value=report)
    display = Mock()
    monkeypatch.setattr(main.ping, "run_ping_diagnostic", diagnostic)
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
    monkeypatch.setattr(main.dns, "dns_query", diagnostic)
    monkeypatch.setattr(main.output, "dns_query_output", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with("google.com")
    display.assert_called_once_with(report)


@pytest.mark.parametrize("latency", [None, 0.0, 12.0])
def test_main_analyzes_dns_latency_only_when_measurement_exists(
    latency, user_input, monkeypatch,
):
    user_input.side_effect = ["2", "2", "4", "5"]
    report = DnsLatencyResult(
        dns_latency_ms=latency,
        dns_status="NOERROR",
        hostname="google.com",
        execution_status=ExecutionStatus.COMPLETED,
    )
    original_report = replace(report)
    diagnostic = Mock(return_value=report)
    classification = LatencyAnalysis(latency_ms=latency, status="EXCELLENT")

    def classify_measurement(data):
        assert data == original_report
        return classification

    analyze = Mock(side_effect=classify_measurement)
    display = Mock()
    monkeypatch.setattr(main.dns, "dns_latency", diagnostic)
    monkeypatch.setattr(main.analysis, "dns_latency_analyzation", analyze)
    monkeypatch.setattr(main.output, "dns_latency_output", display)

    main.main()

    assert user_input.call_count == 4
    diagnostic.assert_called_once_with("google.com")
    if latency is None:
        analyze.assert_not_called()
        assert report == original_report
    else:
        analyze.assert_called_once_with(report)
    assert report == original_report
    display.assert_called_once_with(report, classification if latency is not None else None)


def test_main_starts_both_address_family_lookups_for_each_site(user_input, monkeypatch):
    user_input.side_effect = ["2", "3", "4", "5"]
    sites = ["google.com", "wikipedia.com", "example.com", "yle.fi"]
    reports = [DnsAddressResult(
        hostname=site,
        execution_status=ExecutionStatus.COMPLETED,
        ipv4_addresses=["192.0.2.10"],
        ipv6_addresses=[],
    ) for site in sites]
    diagnostic = Mock(side_effect=reports)
    display = Mock()
    monkeypatch.setattr(main.dns, "ipv_check", diagnostic)
    monkeypatch.setattr(main.output, "ipv_check_results_output", display)

    main.main()

    assert user_input.call_count == 4
    assert diagnostic.call_args_list == [call(site) for site in sites]
    display.assert_called_once_with(reports)


def test_main_displays_listening_socket_counts_and_rows(user_input, monkeypatch):
    user_input.side_effect = ["3", "1", "3", "5"]
    raw = ["tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"]
    parsed = [ListeningSocket(protocol="tcp", state="LISTEN", local_port="22")]
    ports = [ListeningSocket(protocol="TCP", state="LISTEN", local_port="22")]
    counts = SocketCounts(tcp_count=1, udp_count=0)
    collect = Mock(return_value=raw)
    parse = Mock(return_value=parsed)
    select = Mock(return_value=ports)
    count = Mock(return_value=counts)
    header = Mock()
    display_counts = Mock()
    display_ports = Mock()
    monkeypatch.setattr(main.network, "get_listening_sockets", collect)
    monkeypatch.setattr(main.network, "parse_ss_output", parse)
    monkeypatch.setattr(main.analysis, "get_open_ports", select)
    monkeypatch.setattr(main.analysis, "count_open_ports", count)
    monkeypatch.setattr(main.output, "display_open_ports_header", header)
    monkeypatch.setattr(main.output, "display_open_ports_count", display_counts)
    monkeypatch.setattr(main.output, "display_open_ports", display_ports)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    parse.assert_called_once_with(raw)
    select.assert_called_once_with(parsed)
    count.assert_called_once_with(ports)
    header.assert_called_once_with()
    display_counts.assert_called_once_with(counts)
    display_ports.assert_called_once_with(ports)


def test_main_displays_port_service_suggestions(user_input, monkeypatch):
    user_input.side_effect = ["3", "2", "3", "5"]
    raw = ["tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"]
    ports = [ListeningSocket(
        protocol="TCP",
        state="LISTEN",
        local_address="0.0.0.0",
        local_port="22",
        peer_address="0.0.0.0",
        peer_port="*",
    )]
    services = [ServiceInfo(protocol="TCP", local_address="0.0.0.0", port=22, service_name="SSH")]
    collect = Mock(return_value=raw)
    analyze = Mock(return_value=services)
    display = Mock()
    monkeypatch.setattr(main.network, "get_listening_sockets", collect)
    monkeypatch.setattr(main.analysis, "analyze_open_ports", analyze)
    monkeypatch.setattr(main.output, "display_known_services", display)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    analyze.assert_called_once_with(ports)
    display.assert_called_once_with(services)


def test_main_displays_system_routing_snapshot(user_input, monkeypatch):
    user_input.side_effect = ["4", "1", "3", "5"]
    routes = ["default via 192.0.2.1 dev enp3s0 src 192.0.2.10"]
    collect = Mock(return_value=routes)
    hostname = Mock(return_value="workstation")
    interface = Mock(return_value="enp3s0")
    address = Mock(return_value="192.0.2.10")
    gateway = Mock(return_value="192.0.2.1")
    display = Mock()
    monkeypatch.setattr(main.network, "get_ip_route", collect)
    monkeypatch.setattr(main.network, "get_hostname", hostname)
    monkeypatch.setattr(main.network, "get_nic", interface)
    monkeypatch.setattr(main.network, "get_ip", address)
    monkeypatch.setattr(main.network, "get_gateway", gateway)
    monkeypatch.setattr(main.output, "display_routing_table", display)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    hostname.assert_called_once_with()
    interface.assert_called_once_with(routes)
    address.assert_called_once_with(routes)
    gateway.assert_called_once_with(routes)
    display.assert_called_once_with("workstation", "enp3s0", "192.0.2.10", "192.0.2.1", routes)


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
    monkeypatch.setattr(main.network, "nmcli_status", collect)
    monkeypatch.setattr(main.output, "display_network_interface_status", display)

    main.main()

    assert user_input.call_count == 4
    collect.assert_called_once_with()
    display.assert_called_once_with(interfaces)


@pytest.mark.parametrize("error_type", [CommandNotFoundError, CommandTimeoutError, CommandExecutionError, ValidationError])
def test_main_recovers_from_networkmanager_error(error_type, user_input, monkeypatch):
    user_input.side_effect = ["1", "5", "5"]
    status = Mock(side_effect=[error_type("nmcli unavailable"), False])
    display = Mock()
    monkeypatch.setattr(main.network, "get_network_plugin", status)
    monkeypatch.setattr(main.output, "display_command_error", display)

    main.main()

    assert user_input.call_count == 3
    display.assert_called_once_with("nmcli unavailable")
    assert main.output.mainmenu1.call_args_list == [call(None), call(False)]


@pytest.mark.parametrize("menu, action, module, function_name", [
    ("1", "1", main.ping, "run_ping_diagnostic"),
    ("2", "1", main.dns, "dns_query"),
    ("2", "2", main.dns, "dns_latency"),
    ("2", "3", main.dns, "ipv_check"),
    ("3", "1", main.network, "get_listening_sockets"),
    ("3", "2", main.network, "get_listening_sockets"),
    ("4", "1", main.network, "get_ip_route"),
    ("4", "2", main.network, "nmcli_status"),
])
@pytest.mark.parametrize("error_type", [CommandError, CommandNotFoundError, CommandTimeoutError, CommandExecutionError, ValidationError])
def test_main_returns_to_main_menu_after_diagnostic_error(
    menu, action, module, function_name, error_type, user_input, monkeypatch,
):
    user_input.side_effect = [menu, action, "5"]
    diagnostic = Mock(side_effect=error_type("diagnostic failed"))
    display = Mock()
    monkeypatch.setattr(module, function_name, diagnostic)
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


def test_main_displays_snapshot_without_default_route(user_input, monkeypatch):
    user_input.side_effect = ["4", "1", "3", "5"]
    display = Mock()
    monkeypatch.setattr(main.network, "get_ip_route", Mock(return_value=[]))
    monkeypatch.setattr(main.network, "get_hostname", Mock(return_value="workstation"))
    monkeypatch.setattr(main.output, "display_routing_table", display)

    main.main()

    assert user_input.call_count == 4
    display.assert_called_once_with("workstation", None, None, None, [])


@pytest.mark.parametrize("error, message", [
    (FileNotFoundError("missing"), "Required command not found: ip"),
    (subprocess.TimeoutExpired(["ip", "-4", "route"], 10), "ip timed out after 10 seconds."),
    (OSError("Permission denied"), "Could not execute ip: Permission denied"),
])
def test_main_recovers_from_real_runner_errors(error, message, user_input, subprocess_run, monkeypatch):
    user_input.side_effect = ["4", "1", "5"]
    subprocess_run.side_effect = error
    display = Mock()
    monkeypatch.setattr(main.output, "display_command_error", display)

    main.main()

    assert user_input.call_count == 3
    assert main.output.mainmenu1.call_count == 2
    display.assert_called_once_with(message)
    subprocess_run.assert_called_once()


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
        assert "No test packets were received" in fields["reason"]
    else:
        assert fields["reachable"] == "True"
        assert "packets were" in fields["reason"]
    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "3", "8.8.8.8"]
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
    monkeypatch.setattr(main.dns, "dns_query", query)
    monkeypatch.setattr(main.ping, "run_ping_diagnostic", ping_diagnostic)
    monkeypatch.setattr(main.output, "display_command_error", display_error)
    monkeypatch.setattr(main.output, "display_ping_report", display_report)

    main.main()

    query.assert_called_once_with("google.com")
    display_error.assert_called_once_with("getent unavailable")
    ping_diagnostic.assert_called_once_with("localhost")
    display_report.assert_called_once_with(report)
    assert user_input.call_count == 6


def test_main_reports_invalid_route_interface_without_address_command(user_input, subprocess_run, capsys):
    user_input.side_effect = ["4", "1", "5"]
    responses = {
        "ip": subprocess.CompletedProcess([], 0, "default via 192.0.2.1 dev -f\n", ""),
        "hostname": subprocess.CompletedProcess([], 0, "workstation\n", ""),
    }
    subprocess_run.side_effect = lambda arguments, **kwargs: responses[arguments[0]]

    main.main()

    output = capsys.readouterr().out
    assert "Error: Invalid interface name: '-f'" in output
    assert user_input.call_count == 3
    assert main.output.mainmenu1.call_count == 2
    assert [item.args[0] for item in subprocess_run.call_args_list] == [
        ["ip", "-4", "route"], ["hostname"],
    ]
