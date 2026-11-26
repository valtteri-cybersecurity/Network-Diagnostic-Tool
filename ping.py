from command_runner import run_command
from exceptions import CommandError, CommandTimeoutError, DiagnosticError, ParsingError
from models import CommandResult, ExecutionStatus, PingResult
from network import get_gateway, get_ip_route
from analysis import analyze_jitter, analyze_packet_loss, ping_latency_analyzation
from parsers import parse_ping_statistics, select_default_route
from validation import validate_host, validate_ipv4_address


PING_TIMEOUT = 10
PING_COUNT = 3
# Seconds to wait for a reply when no reply has been received (ping -W). The
# iputils default of 10 s made total loss exceed PING_TIMEOUT and appear as a
# technical timeout. Sending takes PING_COUNT - 1 seconds before this wait.
PING_REPLY_WAIT = 2


def _run_ping(target: str) -> CommandResult:
    validate_host(target)
    return run_command(
        ["ping", "-n", "-c", str(PING_COUNT), "-W", str(PING_REPLY_WAIT), target],
        allowed_codes=(0, 1), timeout=PING_TIMEOUT,
    )


def ping_localhost() -> CommandResult:
    return _run_ping("127.0.0.1")


def ping_gateway() -> CommandResult:
    routes = get_ip_route()
    if select_default_route(routes) is None:
        raise DiagnosticError("No IPv4 default route found.")
    gateway = get_gateway(routes)
    if gateway is None:
        # For example, a point-to-point link such as `default dev ppp0`.
        raise DiagnosticError("The IPv4 default route has no gateway address.")
    validate_ipv4_address(gateway)
    return _run_ping(gateway)


def ping_internet_dns() -> CommandResult:
    return _run_ping("google.com")


def ping_internet() -> CommandResult:
    return _run_ping("8.8.8.8")


def ping_target(target: str) -> PingResult:
    if not isinstance(target, str):
        return PingResult(
            target=str(target), error=f"Invalid ping target type: {type(target).__name__}",
        )

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

    try:
        return parse_ping_statistics(result.stdout, target)
    except ParsingError as error:
        return PingResult(target=target, error=str(error), raw_output=result.stdout)


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
