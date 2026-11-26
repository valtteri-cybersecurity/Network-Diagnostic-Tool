import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from exceptions import CommandError, CommandExecutionError, DiagnosticError
from models import CommandResult, ExecutionStatus, PacketLossStatus, PingResult
import ping


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
        ["ping", "-n", "-c", "3", "example.com"],
        capture_output=True, text=True, timeout=10, check=False, shell=False,
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
    gateway = Mock(return_value="192.0.2.1")
    completed = CommandResult(["ping", "192.0.2.1"], "reply", "", 0, 0.1)
    run_ping = Mock(return_value=completed)
    monkeypatch.setattr(ping, "get_gateway", gateway)
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    result = ping.ping_gateway()

    assert result is completed
    gateway.assert_called_once_with()
    run_ping.assert_called_once_with("192.0.2.1")


def test_ping_gateway_reports_missing_gateway_without_running_ping(monkeypatch):
    gateway = Mock(return_value=None)
    run_ping = Mock(side_effect=AssertionError("No ping expected"))
    monkeypatch.setattr(ping, "get_gateway", gateway)
    monkeypatch.setattr(ping, "_run_ping", run_ping)

    with pytest.raises(DiagnosticError, match="Could not detect the default gateway"):
        ping.ping_gateway()

    gateway.assert_called_once_with()
    run_ping.assert_not_called()


def test_ping_gateway_propagates_gateway_command_error(monkeypatch):
    error = CommandError("ip failed")
    run_ping = Mock(side_effect=AssertionError("No ping expected"))
    monkeypatch.setattr(ping, "get_gateway", Mock(side_effect=error))
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


@pytest.mark.parametrize("fixture_name, received, loss, average, maximum, deviation", [
    ("success.txt", 3, 0.0, 12.5, 15.0, 2.041),
    ("partial_loss.txt", 2, 33.3333, 15.0, 20.0, 5.0),
])
def test_get_ping_statistics_parses_successful_measurements(fixture_name, received, loss, average, maximum, deviation):
    output = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")

    result = ping.get_ping_statistics(output, "example.com")

    assert result == PingResult(
        target="example.com", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=received, packet_loss_percent=loss,
        min_rtt_ms=10.0, avg_rtt_ms=average, max_rtt_ms=maximum, mdev_ms=deviation,
        reachable=True, raw_output=output,
    )


def test_get_ping_statistics_accepts_round_trip_statistics():
    output = (FIXTURE_DIRECTORY / "success.txt").read_text(encoding="utf-8")
    output = output.replace("rtt min/avg/max/mdev", "round-trip min/avg/max/stddev")

    result = ping.get_ping_statistics(output, "example.com")

    assert result == PingResult(
        target="example.com", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=3, packet_loss_percent=0.0,
        min_rtt_ms=10.0, avg_rtt_ms=12.5, max_rtt_ms=15.0, mdev_ms=2.041,
        reachable=True, raw_output=output,
    )


def test_get_ping_statistics_reports_total_packet_loss_without_rtt():
    output = (FIXTURE_DIRECTORY / "total_loss.txt").read_text(encoding="utf-8")

    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=0, packet_loss_percent=100.0,
        reachable=False, raw_output=output,
    )


@pytest.mark.parametrize("message", ["command failed", "timeout details", ""])
def test_ping_target_translates_diagnostic_errors_once(message, monkeypatch):
    error = DiagnosticError(message)
    helper = Mock(side_effect=error)
    monkeypatch.setattr(ping, "ping_internet", helper)

    result = ping.ping_target("internet")

    assert result == PingResult(target="internet", error=message)
    helper.assert_called_once_with()


@pytest.mark.parametrize("output", [
    "", " \n", "PING example.com (192.0.2.10) 56(84) bytes of data.\n",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041 ms\n",
])
def test_get_ping_statistics_reports_missing_packet_statistics(output):
    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(target="internet", raw_output=output, error="No packet statistics found")


@pytest.mark.parametrize("output, received, loss", [
    ("3 packets transmitted, 3 received", 3, None),
    ("3 packets transmitted, 0% packet loss", None, 0.0),
    ("3 packets transmitted", None, None),
])
def test_get_ping_statistics_reports_incomplete_packet_statistics(output, received, loss):
    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", raw_output=output, packets_sent=3,
        packets_received=received, packet_loss_percent=loss, error="Incomplete packet statistics",
    )


@pytest.mark.parametrize("output, sent, received", [
    ("invalid packets transmitted, 3 received, 0% packet loss", None, None),
    ("3.5 packets transmitted, 3 received, 0% packet loss", None, None),
    ("3 packets transmitted, invalid received, 0% packet loss", 3, None),
    ("3 packets transmitted, 3 received, invalid% packet loss", 3, 3),
])
def test_get_ping_statistics_reports_invalid_packet_numbers(output, sent, received):
    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", raw_output=output, packets_sent=sent, packets_received=received,
        error="Unable to parse packet statistics",
    )


def test_get_ping_statistics_reports_missing_rtt_after_received_packets():
    output = "3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n"

    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", raw_output=output, packets_sent=3, packets_received=3,
        packet_loss_percent=0.0, reachable=True, error="No latency data available",
    )


@pytest.mark.parametrize("rtt_line", [
    "rtt min/avg/max/mdev 10.000/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041/99 ms",
    "rtt min/avg/max/mdev = invalid/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/invalid/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/invalid/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/invalid ms",
    "rtt min/avg/max/mdev = ",
])
def test_get_ping_statistics_reports_malformed_rtt(rtt_line):
    output = "3 packets transmitted, 3 received, 0% packet loss\n" + rtt_line

    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", raw_output=output, packets_sent=3, packets_received=3,
        packet_loss_percent=0.0, reachable=True, error="Unable to parse latency statistics",
    )


def test_get_ping_statistics_currently_accepts_impossible_packet_counts_and_loss():
    output = (
        "3 packets transmitted, 4 received, 150% packet loss\n"
        "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041 ms\n"
    )

    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=4, packet_loss_percent=150.0,
        min_rtt_ms=10.0, avg_rtt_ms=12.5, max_rtt_ms=15.0, mdev_ms=2.041,
        reachable=True, raw_output=output,
    )


def test_get_ping_statistics_currently_accepts_negative_and_reversed_rtt():
    output = (
        "3 packets transmitted, 3 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 20.000/-1.000/10.000/2.041 ms\n"
    )

    result = ping.get_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=3, packet_loss_percent=0.0,
        min_rtt_ms=20.0, avg_rtt_ms=-1.0, max_rtt_ms=10.0, mdev_ms=2.041,
        reachable=True, raw_output=output,
    )


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
    assert subprocess_run.call_args.args[0] == ["ping", "-n", "-c", "3", "8.8.8.8"]
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


def test_total_packet_loss_is_a_completed_measurement():
    raw = (FIXTURE_DIRECTORY / "total_loss.txt").read_text(encoding="utf-8")

    result = ping.get_ping_statistics(raw, "internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.packets_sent == 3
    assert result.packets_received == 0
    assert result.packet_loss_percent == 100.0
    assert result.reachable is False
    assert result.error is None
    assert result.min_rtt_ms is None
    assert result.avg_rtt_ms is None
    assert result.max_rtt_ms is None
    assert result.mdev_ms is None



def test_ping_target_reports_missing_gateway_as_unknown_reachability(monkeypatch, subprocess_run):
    monkeypatch.setattr(ping, "get_gateway", Mock(return_value=None))

    result = ping.ping_target("gateway")

    assert result == PingResult(target="gateway", error="Could not detect the default gateway.")
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


def test_malformed_rtt_does_not_claim_unreachable_target():
    raw = "3 packets transmitted, 0 received, 100% packet loss\nrtt invalid\n"

    result = ping.get_ping_statistics(raw, "internet")

    assert result.execution_status is ExecutionStatus.FAILED
    assert result.reachable is None
    assert result.error == "Unable to parse latency statistics"


def test_ping_statistics_preserves_zero_rtt_values():
    raw = (
        "3 packets transmitted, 3 received, 0% packet loss\n"
        "rtt min/avg/max/mdev = 0.000/0.000/0.000/0.000 ms\n"
    )

    result = ping.get_ping_statistics(raw, "localhost")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.reachable is True
    assert result.min_rtt_ms == 0.0
    assert result.avg_rtt_ms == 0.0
    assert result.max_rtt_ms == 0.0
    assert result.mdev_ms == 0.0


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
