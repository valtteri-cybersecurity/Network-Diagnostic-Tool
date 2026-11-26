import ipaddress
import re

from command_runner import run_command
from models import CommandResult, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus
from validation import validate_host


def dns_run(site: str) -> CommandResult:
    validate_host(site)
    return run_command(
        ["getent", "hosts", site],
        allowed_codes=(0, 2)
    )


def ipv_check(site: str) -> DnsAddressResult:
    validate_host(site)
    addresses = DnsAddressResult(hostname=site, execution_status=ExecutionStatus.COMPLETED)

    for address_list, database, version in (
        (addresses.ipv4_addresses, "ahostsv4", 4),
        (addresses.ipv6_addresses, "ahostsv6", 6),
    ):
        result = run_command(
            ["getent", database, site],
            allowed_codes=(0, 2)
        )

        if result.return_code != 0:
            continue

        for line in result.stdout.splitlines():
            parts = line.split()

            if not parts:
                continue

            try:
                address = ipaddress.ip_address(parts[0])
            except ValueError:
                continue

            if address.version != version:
                continue

            value = str(address)

            if value not in address_list:
                address_list.append(value)

    return addresses


def dns_query(site: str) -> DnsQueryResult:
    result = dns_run(site)
    text = result.stdout.strip()
    resolved = result.return_code == 0 and bool(text)

    return DnsQueryResult(
        hostname=site, execution_status=ExecutionStatus.COMPLETED,
        resolved=resolved, ip_address=text.split()[0] if resolved else None,
    )


def dns_latency(site: str) -> DnsLatencyResult:
    validate_host(site)
    # -q makes dig treat valid single-label names such as IN or A as the query name.
    result = run_command(
        ["dig", "+time=3", "+tries=1", "+stats", "-q", site]
    )

    status_match = re.search(
        r"status:\s*([A-Z0-9]+)",
        result.stdout
    )

    time_match = re.search(
        r"Query time:\s*(\d+(?:\.\d+)?)\s*msec",
        result.stdout
    )

    answer_match = re.search(
        r"ANSWER:\s*(\d+)",
        result.stdout
    )

    dns_status = status_match.group(1) if status_match else None
    answer_count = int(answer_match.group(1)) if answer_match else None

    resolved = None
    if dns_status is not None:
        if dns_status != "NOERROR":
            resolved = False
        elif answer_count is not None:
            resolved = answer_count > 0

    return DnsLatencyResult(
        hostname=site,
        execution_status=ExecutionStatus.COMPLETED if status_match else ExecutionStatus.FAILED,
        dns_status=dns_status, resolved=resolved,
        dns_latency_ms=float(time_match.group(1)) if time_match else None,
        answer_count=answer_count,
        error=None if status_match else "Unable to parse DNS response status",
    )
