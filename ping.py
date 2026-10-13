import os
import subprocess

from network import get_gateway
from analysis import analyze_jitter, ping_latency_analyzation
from output import display_ping_report, display_ping_result


PING_TIMEOUT = 10
PING_COUNT = 3


def _run_ping(target):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"

    try:
        result = subprocess.run(
            ["ping", "-n", "-c", str(PING_COUNT), target],
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT,
            check=False,
            env=environment
        )

    except subprocess.TimeoutExpired as error:
        partial_output = error.stdout or ""

        if isinstance(partial_output, bytes):
            partial_output = partial_output.decode(
                "utf-8",
                errors="replace"
            )

        return (
            f"PING_ERROR: Ping timed out after {PING_TIMEOUT} seconds.\n"
            f"{partial_output}"
        )

    except FileNotFoundError:
        return "PING_ERROR: The ping command was not found."

    except OSError as error:
        return f"PING_ERROR: Could not execute ping: {error}"

    output = result.stdout.strip()

    if output:
        return output

    error_output = result.stderr.strip()

    if error_output:
        return f"PING_ERROR: {error_output}"

    return (
        f"PING_ERROR: Ping exited with return code "
        f"{result.returncode} without output."
    )


def ping_localhost():
    return _run_ping("127.0.0.1")


def ping_gateway():
    gateway = get_gateway()

    if gateway is None:
        return "PING_ERROR: Could not detect the default gateway."

    return _run_ping(gateway)


def ping_internet_dns():
    return _run_ping("google.com")


def ping_internet():
    return _run_ping("8.8.8.8")


def ping_target(target):
    targets = {
        "localhost": ping_localhost,
        "gateway": ping_gateway,
        "internet": ping_internet,
        "google.com": ping_internet_dns
    }

    ping_function = targets.get(target)

    if ping_function is None:
        return f"PING_ERROR: Unknown ping target: {target}"

    return ping_function()


def get_ping_statistics(result, reason):
    report = {
        "status": "failed",
        "reason": reason,
        "sent": None,
        "received": None,
        "loss": None
    }

    if result.startswith("PING_ERROR:"):
        report["error"] = result.removeprefix("PING_ERROR:").strip()
        return report

    transmitted_report = None
    latency_report = None

    for line in result.splitlines():
        if "transmitted" in line:
            transmitted_report = line

        if line.startswith("rtt ") or line.startswith("round-trip "):
            latency_report = line

    if transmitted_report is None:
        report["error"] = "No packet statistics found"
        return report

    try:
        for action in transmitted_report.split(","):
            action = action.strip()

            if "transmitted" in action:
                report["sent"] = int(action.split()[0])

            elif "received" in action:
                report["received"] = int(action.split()[0])

            elif "packet loss" in action:
                report["loss"] = float(
                    action.split()[0].replace("%", "")
                )

    except (ValueError, IndexError):
        report["error"] = "Unable to parse packet statistics"
        return report

    if any(report[key] is None for key in ("sent", "received", "loss")):
        report["error"] = "Incomplete packet statistics"
        return report

    if latency_report is None:
        report["error"] = "No latency data available"
        return report

    try:
        latency_values = latency_report.split("=", 1)[1]
        latency_values = latency_values.replace(" ms", "").strip()

        min_value, avg_value, max_value, mdev = latency_values.split("/")

        report.update({
            "status": "success",
            "avg": float(avg_value),
            "min": float(min_value),
            "max": float(max_value)
        })

    except (ValueError, IndexError):
        report["error"] = "Unable to parse latency statistics"

    return report


def handle_ping(target):
    result = ping_target(target)

    report = get_ping_statistics(result, "")
    report["reason"] = display_ping_result(result, report)

    if report["status"] != "success":
        display_ping_report(
            report,
            target,
            "Not available",
            "Not available"
        )
        return

    jitter_state = analyze_jitter(
        report["min"],
        report["max"]
    )

    latency_analysis = ping_latency_analyzation(report)

    display_ping_report(
        report,
        target,
        jitter_state,
        latency_analysis
    )
