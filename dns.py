import ipaddress
import re

from network import run_command


def dns_run(site):
    return run_command(
        ["getent", "hosts", site],
        allowed_codes=(0, 2)
    )


def ipv_check(site):
    addresses = {
        "ipv4": [],
        "ipv6": []
    }

    for field, database, version in (
        ("ipv4", "ahostsv4", 4),
        ("ipv6", "ahostsv6", 6)
    ):
        result = run_command(
            ["getent", database, site],
            allowed_codes=(0, 2)
        )

        if result.returncode != 0:
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

            if value not in addresses[field]:
                addresses[field].append(value)

    return {
        "site": site,
        **addresses
    }


def dns_query(site):
    result = dns_run(site)
    text = result.stdout.strip()
    resolved = result.returncode == 0 and bool(text)

    return {
        "site": site,
        "status": "OK" if resolved else "FAILED",
        "resolved": resolved,
        "ip": text.split()[0] if resolved else None
    }


def dns_latency(site):
    result = run_command(
        ["dig", "+time=3", "+tries=1", "+stats", site]
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

    dns_status = status_match.group(1) if status_match else "UNKNOWN"
    answer_count = int(answer_match.group(1)) if answer_match else 0

    return {
        "site": site,
        "dns_latency_ms": float(time_match.group(1)) if time_match else None,
        "dns_status": dns_status,
        "resolved": dns_status == "NOERROR" and answer_count > 0
    }
