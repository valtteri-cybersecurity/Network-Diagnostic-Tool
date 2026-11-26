from command_runner import run_command
from exceptions import CommandError, CommandTimeoutError, DiagnosticError
from models import CommandResult, ExecutionStatus, PingResult
from network import get_gateway
from analysis import analyze_jitter, analyze_packet_loss, ping_latency_analyzation


PING_TIMEOUT = 10
PING_COUNT = 3


def _run_ping(target: str) -> CommandResult:
    return run_command(
        ["ping", "-n", "-c", str(PING_COUNT), target],
        allowed_codes=(0, 1), timeout=PING_TIMEOUT,
    )


def ping_localhost() -> CommandResult:
    return _run_ping("127.0.0.1")


def ping_gateway() -> CommandResult:
    gateway = get_gateway()
    if gateway is None:
        raise DiagnosticError("Could not detect the default gateway.")
    return _run_ping(gateway)


def ping_internet_dns() -> CommandResult:
    return _run_ping("google.com")


def ping_internet() -> CommandResult:
    return _run_ping("8.8.8.8")


def ping_target(target: str) -> PingResult:
    targets = {
        "localhost": ping_localhost,
        "gateway": ping_gateway,
        "internet": ping_internet,
        "google.com": ping_internet_dns,
    }
    ping_function = targets.get(target)
    if ping_function is None:
        return PingResult(target=target, error=f"Unknown ping target: {target}")

    try:
        result = ping_function()
    except CommandTimeoutError as error:
        return PingResult(
            target=target, execution_status=ExecutionStatus.TIMEOUT,
            error=str(error), raw_output=error.stdout,
        )
    except DiagnosticError as error:
        return PingResult(
            target=target, error=str(error),
            raw_output=error.stdout if isinstance(error, CommandError) else "",
        )

    if not result.stdout.strip() and result.stderr.strip():
        return PingResult(target=target, error=result.stderr.strip(), raw_output=result.stdout)
    return get_ping_statistics(result.stdout, target)


def get_ping_statistics(result: str, target: str) -> PingResult:
    report = PingResult(target=target, raw_output=result)

    transmitted_report = None
    latency_report = None

    for line in result.splitlines():
        if "transmitted" in line:
            transmitted_report = line

        if line.startswith("rtt ") or line.startswith("round-trip "):
            latency_report = line

    if transmitted_report is None:
        report.error = "No packet statistics found"
        return report

    try:
        for action in transmitted_report.split(","):
            action = action.strip()

            if "transmitted" in action:
                report.packets_sent = int(action.split()[0])

            elif "received" in action:
                report.packets_received = int(action.split()[0])

            elif "packet loss" in action:
                report.packet_loss_percent = float(
                    action.split()[0].replace("%", "")
                )

    except (ValueError, IndexError):
        report.error = "Unable to parse packet statistics"
        return report

    if any(value is None for value in (
        report.packets_sent, report.packets_received, report.packet_loss_percent,
    )):
        report.error = "Incomplete packet statistics"
        return report

    if report.packets_received > 0:
        report.reachable = True
    if latency_report is None:
        if report.packets_received == 0:
            report.execution_status = ExecutionStatus.COMPLETED
            report.reachable = False
            return report
        report.error = "No latency data available"
        return report

    try:
        latency_values = latency_report.split("=", 1)[1]
        latency_values = latency_values.replace(" ms", "").strip()

        min_value, avg_value, max_value, mdev = latency_values.split("/")

        minimum = float(min_value)
        average = float(avg_value)
        maximum = float(max_value)
        deviation = float(mdev)
        report.min_rtt_ms = minimum
        report.avg_rtt_ms = average
        report.max_rtt_ms = maximum
        report.mdev_ms = deviation
        report.reachable = report.packets_received > 0
        report.execution_status = ExecutionStatus.COMPLETED

    except (ValueError, IndexError):
        report.error = "Unable to parse latency statistics"

    return report


def run_ping_diagnostic(target: str) -> PingResult:
    """Return measurements and analyses without interacting with a user interface."""
    report = ping_target(target)
    report.packet_loss_status = analyze_packet_loss(report.packet_loss_percent)

    if report.execution_status is ExecutionStatus.COMPLETED:
        if report.min_rtt_ms is not None and report.max_rtt_ms is not None:
            report.rtt_range_analysis = analyze_jitter(report.min_rtt_ms, report.max_rtt_ms)
        if report.avg_rtt_ms is not None:
            report.latency_analysis = ping_latency_analyzation(report)

    return report
