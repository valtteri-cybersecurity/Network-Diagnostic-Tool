from command_runner import run_command
from exceptions import ParsingError
from models import CommandResult, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus
from parsers import parse_dig_output, parse_getent_addresses, parse_getent_hosts
from validation import validate_host


DIG_NO_REPLY = 9


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

        address_list.extend(parse_getent_addresses(result.stdout, version))

    return addresses


def dns_query(site: str) -> DnsQueryResult:
    result = dns_run(site)

    address = None
    if result.return_code == 0:
        try:
            address = parse_getent_hosts(result.stdout)
        except ParsingError as error:
            return DnsQueryResult(hostname=site, execution_status=ExecutionStatus.FAILED, error=str(error))

    return DnsQueryResult(
        hostname=site, execution_status=ExecutionStatus.COMPLETED,
        resolved=address is not None, ip_address=address,
    )


def dns_latency(site: str) -> DnsLatencyResult:
    validate_host(site)
    # -q makes dig treat valid single-label names such as IN or A as the query name.
    # Exit code 9 means that no DNS server replied. It is a measurement result,
    # not a failure of dig itself, so the name's resolution remains unknown.
    result = run_command(
        ["dig", "+time=3", "+tries=1", "+stats", "-q", site],
        allowed_codes=(0, DIG_NO_REPLY),
    )

    if result.return_code == DIG_NO_REPLY:
        status = ExecutionStatus.FAILED
        # dig versions differ in whether this message goes to stdout or stderr.
        if "timed out" in result.stdout + result.stderr:
            status = ExecutionStatus.TIMEOUT
        return DnsLatencyResult(hostname=site, execution_status=status, error="No reply from DNS server")

    try:
        return parse_dig_output(result.stdout, site)
    except ParsingError as error:
        return DnsLatencyResult(hostname=site, execution_status=ExecutionStatus.FAILED, error=str(error))
