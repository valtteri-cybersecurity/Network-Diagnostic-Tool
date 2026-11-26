import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from network_diagnostic.core.exceptions import CommandError, CommandExecutionError, DiagnosticError, ValidationError
from network_diagnostic.core.models import CommandResult, ExecutionStatus, PacketLossStatus, PingResult
from network_diagnostic.core import ping


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "ping"


def test_run_ping_uses_expected_arguments_and_c_locale(monkeypatch, subprocess_run):
    output = (FIXTURE_DIRECTORY / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, output, "")
    monkeypatch.setenv("LC_ALL", "fi_FI.UTF-8")
    original_environment = os.environ.copy()

    result = ping._run_ping("example.com")

    assert isinstance(result, CommandResult)
    assert result.stdout == output
    assert result.return_code == 0
    subprocess_run.assert_called_once_with(
        ["ping", "-n", "-c", "3", "-W", "2", "example.com"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False, shell=False,
        env={**original_environment, "LC_ALL": "C"},
    )
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize("fixture_name, returncode", [
    ("success.txt", 0), ("partial_loss.txt", 0),
    ("partial_loss.txt", 1), ("total_loss.txt", 1),
])
def test_run_ping_returns_statistics_for_success_and_packet_loss(fixture_name, returncode, subprocess_run):
    output = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], returncode, output, "")

    result = ping._run_ping("192.0.2.10")

    assert result.stdout == output
    assert result.return_code == returncode


@pytest.mark.parametrize("error, expected", [
    (FileNotFoundError("missing"), "Required command not found: ping"),
    (PermissionError("Operation not permitted"), "Could not execute ping: Operation not permitted"),
    (OSError("Too many open files"), "Could not execute ping: Too many open files"),
])
def test_ping_target_reports_execution_errors(error, expected, subprocess_run):
    subprocess_run.side_effect = error

    result = ping.ping_target("internet")

    assert result == PingResult(target="internet", error=expected)
    subprocess_run.assert_called_once()


@pytest.mark.parametrize("partial_output, expected_output", [
    (None, ""), ("", ""), (b"", ""),
    ("64 bytes from 192.0.2.10: icmp_seq=1 time=10.0 ms\n",
     "64 bytes from 192.0.2.10: icmp_seq=1 time=10.0 ms\n"),
    (b"64 bytes from 192.0.2.10: icmp_seq=1 time=10.0 ms\n",
     "64 bytes from 192.0.2.10: icmp_seq=1 time=10.0 ms\n"),
    (b"reply: \xff\n", "reply: \ufffd\n"),
])
def test_ping_target_reports_timeout_and_preserves_partial_output(partial_output, expected_output, subprocess_run):
    subprocess_run.side_effect = subprocess.TimeoutExpired(
        ["ping", "8.8.8.8"], 10, output=partial_output,
    )

    result = ping.ping_target("internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.TIMEOUT,
        error="ping timed out after 10 seconds.", raw_output=expected_output,
    )


@pytest.mark.parametrize("returncode", [0, 1, 2])
@pytest.mark.parametrize("output", ["", " \n\t"])
def test_ping_target_reports_failure_without_measurement(returncode, output, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], returncode, output, " \n")

    result = ping.ping_target("internet")

    assert result == PingResult(
        target="internet", raw_output=output,
        error="ping failed: exit code 2" if returncode == 2 else "No packet statistics found",
    )


@pytest.mark.parametrize("returncode", [1, 2])
def test_ping_target_reports_stderr_when_stdout_is_empty(returncode, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], returncode, "", "  ping: connect: Network is unreachable\n",
    )

    result = ping.ping_target("internet")

    expected_error = "ping: connect: Network is unreachable"
    if returncode == 2:
        expected_error = "ping failed: " + expected_error
    assert result == PingResult(target="internet", error=expected_error)


@pytest.mark.parametrize("returncode", [2, 3, -15])
def test_run_ping_reports_error_returncode_even_with_stdout(returncode, subprocess_run):
    subprocess_run.side_effect = None
    raw = "PING example.com (192.0.2.10) 56(84) bytes of data.\n"
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], returncode, raw, "ping: sendmsg: Operation not permitted\n",
    )

    result = ping.ping_target("internet")

    assert result == PingResult(
        target="internet", raw_output=raw,
        error="ping failed: ping: sendmsg: Operation not permitted",
    )


@pytest.mark.parametrize("function, expected_target", [
    (ping.ping_localhost, "127.0.0.1"), (ping.ping_internet, "8.8.8.8"),
    (ping.ping_internet_dns, "google.com"),
])
def test_ping_helpers_use_expected_targets(function, expected_target, monkeypatch):
    completed = CommandResult(["ping", expected_target], "reply", "", 0, 0.1)
    run_ping = Mock(return_value=completed)
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    result = function()

    assert result is completed
    run_ping.assert_called_once_with(expected_target)


def test_ping_gateway_uses_detected_gateway(monkeypatch):
    routes = ["default via 192.0.2.1 dev enp3s0"]
    route_command = Mock(return_value=routes)
    gateway = Mock(return_value="192.0.2.1")
    completed = CommandResult(["ping", "192.0.2.1"], "reply", "", 0, 0.1)
    run_ping = Mock(return_value=completed)
    monkeypatch.setattr(ping, "get_ip_route", route_command)
    monkeypatch.setattr(ping, "get_gateway", gateway)
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    result = ping.ping_gateway()

    assert result is completed
    route_command.assert_called_once_with()
    gateway.assert_called_once_with(routes)
    run_ping.assert_called_once_with("192.0.2.1")


def test_ping_gateway_propagates_gateway_command_error(monkeypatch):
    error = CommandError("ip failed")
    run_ping = Mock(side_effect=AssertionError("No ping expected"))
    monkeypatch.setattr(ping, "get_ip_route", Mock(side_effect=error))
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    with pytest.raises(CommandError) as caught:
        ping.ping_gateway()

    assert caught.value is error
    run_ping.assert_not_called()


@pytest.mark.parametrize("target, function_name", [
    ("localhost", "ping_localhost"), ("gateway", "ping_gateway"),
    ("internet", "ping_internet"), ("google.com", "ping_internet_dns"),
])
def test_ping_target_dispatches_to_matching_helper(target, function_name, monkeypatch):
    raw = (FIXTURE_DIRECTORY / "success.txt").read_text(encoding="utf-8")
    helper = Mock(return_value=CommandResult([], raw, "", 0, 0.1))
    monkeypatch.setattr(ping, function_name, helper)

    result = ping.ping_target(target)

    assert result.target == target
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.raw_output == raw
    assert result.packets_received == 3
    helper.assert_called_once_with()


@pytest.mark.parametrize("target", ["", "unknown", "8.8.8.8"])
def test_ping_target_reports_unknown_target(target, subprocess_run):
    result = ping.ping_target(target)

    assert result == PingResult(target=target, error=f"Unknown ping target: {target}")
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("message", ["command failed", "timeout details", ""])
def test_ping_target_translates_diagnostic_errors_once(message, monkeypatch):
    error = DiagnosticError(message)
    helper = Mock(side_effect=error)
    monkeypatch.setattr(ping, "ping_internet", helper)

    result = ping.ping_target("internet")

    assert result == PingResult(target="internet", error=message)
    helper.assert_called_once_with()


@pytest.mark.parametrize("fixture_name, received, loss, average, maximum, deviation", [
    ("success.txt", 3, 0.0, 12.5, 15.0, 2.041),
    ("partial_loss.txt", 2, 33.3333, 15.0, 20.0, 5.0),
])
def test_run_ping_diagnostic_returns_measurements_and_calls_analyzers(
    fixture_name, received, loss, average, maximum, deviation, monkeypatch, capsys,
):
    raw = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    report = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=received, packet_loss_percent=loss,
        min_rtt_ms=10.0, avg_rtt_ms=average, max_rtt_ms=maximum, mdev_ms=deviation,
        reachable=True, raw_output=raw,
    )
    target = Mock(return_value=report)
    jitter = Mock(return_value="Good")
    latency = Mock(return_value="OK")
    monkeypatch.setattr(ping, "ping_target", target)
    monkeypatch.setattr(ping, "analyze_jitter", jitter)
    monkeypatch.setattr(ping, "ping_latency_analyzation", latency)

    result = ping.run_ping_diagnostic("internet")

    assert result is report
    target.assert_called_once_with("internet")
    jitter.assert_called_once_with(10.0, maximum)
    latency.assert_called_once_with(report)
    assert result.rtt_range_analysis == "Good"
    assert result.latency_analysis == "OK"
    assert result.packet_loss_status is (PacketLossStatus.NO_LOSS if loss == 0 else PacketLossStatus.PARTIAL_LOSS)
    assert result.error is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_diagnostic_returns_total_loss_without_calling_rtt_analyzers(monkeypatch, capsys):
    raw = (FIXTURE_DIRECTORY / "total_loss.txt").read_text(encoding="utf-8")
    report = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=0, packet_loss_percent=100.0,
        reachable=False, raw_output=raw,
    )
    jitter = Mock(side_effect=AssertionError("No jitter analysis expected"))
    latency = Mock(side_effect=AssertionError("No latency analysis expected"))
    monkeypatch.setattr(ping, "ping_target", Mock(return_value=report))
    monkeypatch.setattr(ping, "analyze_jitter", jitter)
    monkeypatch.setattr(ping, "ping_latency_analyzation", latency)

    result = ping.run_ping_diagnostic("internet")

    assert result is report
    assert result.packet_loss_status is PacketLossStatus.TOTAL_LOSS
    assert result.rtt_range_analysis is None
    assert result.latency_analysis is None
    jitter.assert_not_called()
    latency.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_diagnostic_returns_command_failure_without_analysis(monkeypatch, capsys):
    report = PingResult(target="localhost", error="Required command not found: ping")
    monkeypatch.setattr(ping, "ping_target", Mock(return_value=report))
    jitter = Mock(side_effect=AssertionError("No jitter analysis expected"))
    latency = Mock(side_effect=AssertionError("No latency analysis expected"))
    monkeypatch.setattr(ping, "analyze_jitter", jitter)
    monkeypatch.setattr(ping, "ping_latency_analyzation", latency)

    result = ping.run_ping_diagnostic("localhost")

    assert result is report
    assert result.execution_status is ExecutionStatus.FAILED
    assert result.error == "Required command not found: ping"
    assert result.packet_loss_status is PacketLossStatus.UNAVAILABLE
    assert result.latency_analysis is None
    assert result.rtt_range_analysis is None
    jitter.assert_not_called()
    latency.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_diagnostic_propagates_unexpected_boundary_error(monkeypatch, capsys):
    error = CommandError("unexpected boundary error")
    monkeypatch.setattr(ping, "ping_target", Mock(side_effect=error))

    with pytest.raises(CommandError) as caught:
        ping.run_ping_diagnostic("gateway")

    assert caught.value is error
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("fixture_name, returncode, received, loss, loss_status, latency, variation", [
    ("success.txt", 0, 3, 0.0, PacketLossStatus.NO_LOSS, "OK", "Good"),
    ("partial_loss.txt", 0, 2, 33.3333, PacketLossStatus.PARTIAL_LOSS, "OK", "Good"),
    ("total_loss.txt", 1, 0, 100.0, PacketLossStatus.TOTAL_LOSS, None, None),
])
def test_run_ping_diagnostic_integrates_command_parsing_and_analysis_silently(
    fixture_name, returncode, received, loss, loss_status, latency, variation, subprocess_run, capsys,
):
    raw = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], returncode, raw, "")

    result = ping.run_ping_diagnostic("internet")

    assert isinstance(result, PingResult)
    assert result.target == "internet"
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.packets_sent == 3
    assert result.packets_received == received
    assert result.packet_loss_percent == loss
    assert result.packet_loss_status is loss_status
    assert result.latency_analysis == latency
    assert result.rtt_range_analysis == variation
    assert result.reachable is (received > 0)
    assert result.raw_output == raw
    assert result.error is None
    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "3", "-W", "2", "8.8.8.8"]
    subprocess_run.assert_called_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_reports_error_code_with_stdout_without_stderr(subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 2, "PING example.com\n", "")

    with pytest.raises(CommandExecutionError, match="ping failed: exit code 2") as caught:
        ping._run_ping("example.com")

    assert caught.value.stdout == "PING example.com\n"
    assert caught.value.return_code == 2


def test_run_ping_diagnostic_returns_technical_failure_instead_of_stdout_statistics(subprocess_run, capsys):
    raw = (FIXTURE_DIRECTORY / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], 2, raw, "ping: sendmsg: Operation not permitted\n",
    )

    result = ping.run_ping_diagnostic("internet")

    assert result == PingResult(
        target="internet", error="ping failed: ping: sendmsg: Operation not permitted", raw_output=raw,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_ping_target_reports_missing_gateway_as_unknown_reachability(monkeypatch, subprocess_run):
    monkeypatch.setattr(ping, "get_ip_route", Mock(return_value=[]))

    result = ping.ping_target("gateway")

    assert result == PingResult(target="gateway", error="No IPv4 default route found.")
    subprocess_run.assert_not_called()


def test_timeout_does_not_turn_partial_statistics_into_completed_measurement(subprocess_run):
    raw = (FIXTURE_DIRECTORY / "success.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = subprocess.TimeoutExpired(["ping"], 10, output=raw)

    result = ping.ping_target("internet")

    assert result.execution_status is ExecutionStatus.TIMEOUT
    assert result.raw_output == raw
    assert result.packets_received is None
    assert result.reachable is None
    assert result.avg_rtt_ms is None


def test_run_ping_diagnostic_returns_timeout_status_and_partial_output(subprocess_run, capsys):
    subprocess_run.side_effect = subprocess.TimeoutExpired(["ping"], 10, output=b"partial reply\n")

    result = ping.run_ping_diagnostic("internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.TIMEOUT,
        raw_output="partial reply\n", error="ping timed out after 10 seconds.",
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_diagnostic_returns_missing_command_without_raw_output(subprocess_run, capsys):
    subprocess_run.side_effect = FileNotFoundError("missing")

    result = ping.run_ping_diagnostic("internet")

    assert result == PingResult(target="internet", error="Required command not found: ping")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_ping_diagnostic_analyzes_zero_rtt_instead_of_treating_it_as_missing(subprocess_run, capsys):
    raw = (
        "3 packets transmitted, 3 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 0.000/0.000/0.000/0.000 ms\n"
    )
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = ping.run_ping_diagnostic("localhost")

    assert result == PingResult(
        target="localhost", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=3, packet_loss_percent=0.0,
        min_rtt_ms=0.0, avg_rtt_ms=0.0, max_rtt_ms=0.0, mdev_ms=0.0,
        reachable=True, raw_output=raw, packet_loss_status=PacketLossStatus.NO_LOSS,
        latency_analysis="Excellent", rtt_range_analysis="Excellent",
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("target", [
    "", "-f", "--help", "+trace", "@192.0.2.1", "999.1.2.3", "fe80::1%eth0",
    "example.com;id", "example.com\nid", "example.com\0", None,
])
def test_run_ping_rejects_invalid_target_before_running_command(target, subprocess_run):
    with pytest.raises(ValidationError):
        ping._run_ping(target)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("gateway", [
    "-f", "--help", "gateway.example", "999.1.2.3", "2001:db8::1", "192.0.2.1;id", "",
])
def test_ping_gateway_rejects_non_ipv4_gateway_without_running_ping(gateway, monkeypatch):
    run_ping = Mock(side_effect=AssertionError("No ping expected"))
    monkeypatch.setattr(ping, "get_ip_route", Mock(return_value=["default dev enp3s0"]))
    monkeypatch.setattr(ping, "get_gateway", Mock(return_value=gateway))
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    with pytest.raises(ValidationError):
        ping.ping_gateway()

    run_ping.assert_not_called()


def test_ping_target_reports_invalid_route_gateway_after_route_query_only(subprocess_run, capsys):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], 0, "default via -f dev enp3s0\n", "",
    )

    result = ping.run_ping_diagnostic("gateway")

    assert result.target == "gateway"
    assert result.execution_status is ExecutionStatus.FAILED
    assert result.error == "Invalid default gateway: Invalid IPv4 address: '-f'"
    assert result.raw_output == ""
    assert result.reachable is None
    assert result.latency_analysis is None
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["ip", "-4", "route"]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("target", [None, 1, ["gateway"], {"internet": 1}])
def test_ping_target_reports_wrong_selector_type_without_running_command(target, subprocess_run):
    result = ping.ping_target(target)

    assert result.execution_status is ExecutionStatus.FAILED
    assert result.target == str(target)
    assert result.error == f"Invalid ping target type: {type(target).__name__}"
    subprocess_run.assert_not_called()


PING_SUMMARY = "3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n"
PING_RTT = "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041 ms\n"


@pytest.mark.parametrize("raw, error", [
    ("3 packets transmitted, 4 received, 150% packet loss\n" + PING_RTT,
     "Invalid packet statistics: more packets received than transmitted"),
    ("3 packets transmitted, 3 received, 150% packet loss\n" + PING_RTT,
     "Invalid packet statistics: packet loss outside 0-100%"),
    ("3 packets transmitted, 3 received, 100% packet loss\n" + PING_RTT,
     "Invalid packet statistics: packet loss does not match packet counts"),
    ("3 packets transmitted, 1 received, 33.3333% packet loss\n" + PING_RTT,
     "Invalid packet statistics: packet loss does not match packet counts"),
    ("0 packets transmitted, 0 received, 0% packet loss\n",
     "Invalid packet statistics: no packets transmitted"),
    ("3 packets transmitted, -1 received, 0% packet loss\n" + PING_RTT,
     "Unable to parse packet statistics"),
    ("3 packets transmitted, 3 received, -5% packet loss\n" + PING_RTT,
     "Unable to parse packet statistics"),
    ("3 packets transmitted, 3 received, nan% packet loss\n" + PING_RTT,
     "Unable to parse packet statistics"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = 20.000/-1.000/10.000/2.041 ms\n",
     "Unable to parse latency statistics"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = nan/nan/nan/nan ms\n",
     "Unable to parse latency statistics"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = inf/inf/inf/0.000 ms\n",
     "Unable to parse latency statistics"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = 1e1/1e1/1e1/0 ms\n",
     "Unable to parse latency statistics"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = 20.000/12.500/10.000/2.041 ms\n",
     "Invalid latency statistics: expected min <= avg <= max"),
    (PING_SUMMARY + "rtt min/avg/max/mdev = 10.000/16.000/15.000/2.041 ms\n",
     "Invalid latency statistics: expected min <= avg <= max"),
    ("3 packets transmitted, 0 received, 100% packet loss\n" + PING_RTT,
     "Invalid latency statistics: latency reported without received packets"),
    (PING_SUMMARY + PING_SUMMARY + PING_RTT, "Multiple packet statistics found"),
    (PING_SUMMARY + PING_RTT + PING_RTT, "Multiple latency statistics found"),
])
def test_ping_target_rejects_implausible_or_contradictory_statistics(raw, error, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = ping.ping_target("internet")

    assert result == PingResult(target="internet", error=error, raw_output=raw)


@pytest.mark.parametrize("raw, received, loss", [
    ("3 packets transmitted, 2 received, 33.3333% packet loss\n", 2, 33.3333),
    ("3 packets transmitted, 2 received, 33.3% packet loss\n", 2, 33.3),
    ("3 packets transmitted, 2 packets received, 33% packet loss\n", 2, 33.0),
    ("3 packets transmitted, 3 received, +1 duplicates, 0% packet loss\n", 3, 0.0),
])
def test_ping_target_accepts_rounded_loss_and_reported_duplicates(raw, received, loss, subprocess_run):
    raw = raw + PING_RTT
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = ping.ping_target("internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.error is None
    assert result.packets_received == received
    assert result.packet_loss_percent == loss


# --- Phase 10 regressions -----------------------------------------------------


def test_run_ping_limits_reply_wait_below_command_timeout(subprocess_run):
    # Without -W, iputils waits 10 s for a first reply after the last packet, so
    # total loss exceeded PING_TIMEOUT and was reported as a technical timeout.
    output = (FIXTURE_DIRECTORY / "total_loss.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 1, output, "")

    ping._run_ping("192.0.2.1")

    arguments = subprocess_run.call_args.args[0]
    assert arguments == ["ping", "-n", "-c", "3", "-W", "2", "192.0.2.1"]
    assert ping.PING_COUNT - 1 + ping.PING_REPLY_WAIT < ping.PING_TIMEOUT


@pytest.mark.parametrize("routes, message", [
    ([], "No IPv4 default route found."),
    (["192.0.2.0/24 dev enp3s0 src 192.0.2.10"], "No IPv4 default route found."),
    (["default dev ppp0 scope link"], "The IPv4 default route has no gateway address."),
])
def test_ping_gateway_distinguishes_missing_route_from_route_without_gateway(routes, message, monkeypatch):
    run_ping = Mock(side_effect=AssertionError("No ping expected"))
    monkeypatch.setattr(ping, "get_ip_route", Mock(return_value=routes))
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    with pytest.raises(DiagnosticError, match="^" + message.replace(".", r"\.") + "$"):
        ping.ping_gateway()

    run_ping.assert_not_called()


def test_ping_gateway_pings_gateway_of_lowest_metric_route(monkeypatch):
    routes = ["default via 198.51.100.1 dev wlan0 metric 600", "default via 192.0.2.1 dev enp3s0 metric 100"]
    completed = CommandResult(["ping"], "reply", "", 0, 0.1)
    run_ping = Mock(return_value=completed)
    monkeypatch.setattr(ping, "get_ip_route", Mock(return_value=routes))
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    result = ping.ping_gateway()

    assert result is completed
    run_ping.assert_called_once_with("192.0.2.1")


def test_run_ping_diagnostic_keeps_icmp_errors_of_unreachable_target(subprocess_run, monkeypatch):
    output = (FIXTURE_DIRECTORY / "total_loss.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 1, output, "")

    result = ping.run_ping_diagnostic("internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.packet_loss_status is PacketLossStatus.TOTAL_LOSS
    assert result.icmp_errors == 3


def test_ping_gateway_reports_multipath_route_as_unsupported_instead_of_missing_gateway(monkeypatch, subprocess_run):
    # Phase 13 regression: a multipath default route was reported as
    # "The IPv4 default route has no gateway address".
    routes = ["default proto static metric 100", "\tnexthop via 192.0.2.1 dev enp3s0 weight 1"]
    monkeypatch.setattr(ping, "get_ip_route", Mock(return_value=routes))

    result = ping.ping_target("gateway")

    assert result.execution_status is ExecutionStatus.FAILED
    assert result.error == (
        "Unsupported default route: no single output interface (for example, a multipath route)"
    )
    assert result.reachable is None
    subprocess_run.assert_not_called()
