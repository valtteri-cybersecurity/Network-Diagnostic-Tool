import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

import dns
from exceptions import CommandError, ValidationError
from models import (
    CommandResult, DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
)


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "dns"


@pytest.mark.parametrize("returncode", [0, 2])
def test_dns_run_uses_getent_and_allows_not_found(returncode, monkeypatch):
    output = (FIXTURE_DIRECTORY / "getent_hosts.txt").read_text(encoding="utf-8")
    completed = CommandResult([], output, "", returncode, 0.0)
    command = Mock(return_value=completed)
    monkeypatch.setattr(dns, "run_command", command)

    result = dns.dns_run("example.com")

    assert result is completed
    command.assert_called_once_with(
        ["getent", "hosts", "example.com"], allowed_codes=(0, 2),
    )


def test_ipv_check_collects_both_address_families_and_removes_duplicates(monkeypatch):
    ipv4_output = (FIXTURE_DIRECTORY / "getent_ahostsv4.txt").read_text(encoding="utf-8")
    ipv6_output = (FIXTURE_DIRECTORY / "getent_ahostsv6.txt").read_text(encoding="utf-8")
    command = Mock(side_effect=[
        CommandResult([], ipv4_output, "", 0, 0.0),
        CommandResult([], ipv6_output, "", 0, 0.0),
    ])
    monkeypatch.setattr(dns, "run_command", command)

    result = dns.ipv_check("example.com")

    assert result == DnsAddressResult(
        hostname="example.com",
        ipv4_addresses=["192.0.2.10", "192.0.2.20"],
        ipv6_addresses=["2001:db8::10", "2001:db8::20"],
        execution_status=ExecutionStatus.COMPLETED,
    )
    assert command.call_args_list == [
        call(["getent", "ahostsv4", "example.com"], allowed_codes=(0, 2)),
        call(["getent", "ahostsv6", "example.com"], allowed_codes=(0, 2)),
    ]


def test_ipv_check_skips_invalid_addresses_and_unexpected_address_families(monkeypatch):
    ipv4_output = (
        "\ninvalid STREAM example.com\n999.1.2.3 DGRAM\n"
        "2001:db8::10 STREAM\n192.0.2.10 STREAM\n"
    )
    ipv6_output = (
        " \n2001:db8::invalid STREAM\n192.0.2.10 STREAM\n"
        "2001:0db8:0:0:0:0:0:10 STREAM\n2001:db8::10 DGRAM\n"
    )
    monkeypatch.setattr(dns, "run_command", Mock(side_effect=[
        CommandResult([], ipv4_output, "", 0, 0.0),
        CommandResult([], ipv6_output, "", 0, 0.0),
    ]))

    result = dns.ipv_check("example.com")

    assert result == DnsAddressResult(
        hostname="example.com",
        ipv4_addresses=["192.0.2.10"],
        ipv6_addresses=["2001:db8::10"],
        execution_status=ExecutionStatus.COMPLETED,
    )


@pytest.mark.parametrize("output", ["", " \n\t"])
def test_ipv_check_returns_empty_lists_for_empty_output(output, monkeypatch):
    command = Mock(return_value=CommandResult([], output, "", 0, 0.0))
    monkeypatch.setattr(dns, "run_command", command)

    result = dns.ipv_check("example.com")

    assert result == DnsAddressResult(
        hostname="example.com",
        ipv4_addresses=[],
        ipv6_addresses=[],
        execution_status=ExecutionStatus.COMPLETED,
    )
    assert command.call_count == 2


@pytest.mark.parametrize("ipv4_code, ipv6_code, expected_ipv4, expected_ipv6", [
    (2, 0, [], ["2001:db8::10", "2001:db8::20"]),
    (0, 2, ["192.0.2.10", "192.0.2.20"], []),
    (2, 2, [], []),
])
def test_ipv_check_skips_failed_family_and_continues_other_lookup(
    ipv4_code, ipv6_code, expected_ipv4, expected_ipv6, monkeypatch,
):
    ipv4_output = (FIXTURE_DIRECTORY / "getent_ahostsv4.txt").read_text(encoding="utf-8")
    ipv6_output = (FIXTURE_DIRECTORY / "getent_ahostsv6.txt").read_text(encoding="utf-8")
    command = Mock(side_effect=[
        CommandResult([], ipv4_output, "", ipv4_code, 0.0),
        CommandResult([], ipv6_output, "", ipv6_code, 0.0),
    ])
    monkeypatch.setattr(dns, "run_command", command)

    result = dns.ipv_check("example.com")

    assert result == DnsAddressResult(
        hostname="example.com",
        ipv4_addresses=expected_ipv4,
        ipv6_addresses=expected_ipv6,
        execution_status=ExecutionStatus.COMPLETED,
    )
    assert command.call_count == 2


def test_dns_query_uses_first_resolved_address(monkeypatch):
    output = (FIXTURE_DIRECTORY / "getent_hosts.txt").read_text(encoding="utf-8")
    lookup = Mock(return_value=CommandResult([], "\n" + output, "", 0, 0.0))
    monkeypatch.setattr(dns, "dns_run", lookup)

    result = dns.dns_query("example.com")

    assert result == DnsQueryResult(
        resolved=True,
        hostname="example.com",
        ip_address="2001:db8::10",
        execution_status=ExecutionStatus.COMPLETED,
    )
    lookup.assert_called_once_with("example.com")


def test_dns_query_resolves_ipv4_address(monkeypatch):
    monkeypatch.setattr(dns, "dns_run", Mock(return_value=CommandResult([], "192.0.2.10 example.com\n", "", 0, 0.0)))

    result = dns.dns_query("example.com")

    assert result == DnsQueryResult(
        resolved=True,
        hostname="example.com",
        ip_address="192.0.2.10",
        execution_status=ExecutionStatus.COMPLETED,
    )


@pytest.mark.parametrize("returncode, output", [
    (2, ""),
    (2, "192.0.2.10 example.com\n"),
    (0, ""),
    (0, " \n\t"),
])
def test_dns_query_reports_failed_or_empty_lookup(returncode, output, monkeypatch):
    monkeypatch.setattr(dns, "dns_run", Mock(return_value=CommandResult([], output, "", returncode, 0.0)))

    result = dns.dns_query("example.com")

    assert result == DnsQueryResult(
        resolved=False,
        hostname="example.com",
        ip_address=None,
        execution_status=ExecutionStatus.COMPLETED,
    )


def test_dns_query_currently_accepts_invalid_address_with_success_code(monkeypatch):
    # Current validation gap; see KNOWN_ISSUES.md.
    monkeypatch.setattr(dns, "dns_run", Mock(return_value=CommandResult([], "not-an-address example.com\n", "", 0, 0.0)))

    result = dns.dns_query("example.com")

    assert result == DnsQueryResult(
        resolved=True,
        hostname="example.com",
        ip_address="not-an-address",
        execution_status=ExecutionStatus.COMPLETED,
    )


@pytest.mark.parametrize("fixture_name, site, status, latency, resolved", [
    ("dig_noerror.txt", "example.com", "NOERROR", 12.0, True),
    ("dig_nxdomain.txt", "missing.example.com", "NXDOMAIN", 4.0, False),
    ("dig_servfail.txt", "example.com", "SERVFAIL", 8.0, False),
])
def test_dns_latency_parses_response_and_uses_bounded_dig_query(
    fixture_name, site, status, latency, resolved, monkeypatch,
):
    output = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    command = Mock(return_value=CommandResult([], output, "", 0, 0.0))
    monkeypatch.setattr(dns, "run_command", command)

    result = dns.dns_latency(site)

    assert result == DnsLatencyResult(
        dns_latency_ms=latency,
        dns_status=status,
        resolved=resolved,
        hostname=site,
        execution_status=ExecutionStatus.COMPLETED,
        answer_count=2 if status == "NOERROR" else 0,
    )
    command.assert_called_once_with(["dig", "+time=3", "+tries=1", "+stats", "-q", site])


@pytest.mark.parametrize("old_text, new_text, status, latency, answers, resolved", [
    ("status: NOERROR", "response: NOERROR", None, 12.0, 2, None),
    (";; Query time: 12 msec", "", "NOERROR", None, 2, True),
    ("ANSWER: 2", "RECORDS: 2", "NOERROR", 12.0, None, None),
    ("ANSWER: 2", "ANSWER: 0", "NOERROR", 12.0, 0, False),
    ("Query time: 12 msec", "Query time: 0 msec", "NOERROR", 0.0, 2, True),
    ("Query time: 12 msec", "Query time: 0.125 msec", "NOERROR", 0.125, 2, True),
    ("Query time: 12 msec", "Query time: invalid msec", "NOERROR", None, 2, True),
    ("ANSWER: 2", "ANSWER: invalid", "NOERROR", 12.0, None, None),
])
def test_dns_latency_handles_missing_fields_and_measurement_values(
    old_text, new_text, status, latency, answers, resolved, monkeypatch,
):
    output = (FIXTURE_DIRECTORY / "dig_noerror.txt").read_text(encoding="utf-8")
    output = output.replace(old_text, new_text)
    monkeypatch.setattr(dns, "run_command", Mock(return_value=CommandResult([], output, "", 0, 0.0)))

    result = dns.dns_latency("example.com")

    assert result == DnsLatencyResult(
        hostname="example.com",
        execution_status=ExecutionStatus.COMPLETED if status is not None else ExecutionStatus.FAILED,
        dns_status=status,
        resolved=resolved,
        dns_latency_ms=latency,
        answer_count=answers,
        error=None if status is not None else "Unable to parse DNS response status",
    )


@pytest.mark.parametrize("output", ["", " \n", "unrecognized dig output\n"])
def test_dns_latency_handles_empty_or_unrecognized_output(output, monkeypatch):
    monkeypatch.setattr(dns, "run_command", Mock(return_value=CommandResult([], output, "", 0, 0.0)))

    result = dns.dns_latency("example.com")

    assert result == DnsLatencyResult(
        dns_latency_ms=None,
        dns_status=None,
        resolved=None,
        hostname="example.com",
        error='Unable to parse DNS response status',
    )


@pytest.mark.parametrize("function, command_name", [
    (dns.dns_run, "getent"),
    (dns.ipv_check, "getent"),
    (dns.dns_query, "getent"),
    (dns.dns_latency, "dig"),
])
@pytest.mark.parametrize("error, message", [
    (FileNotFoundError("missing"), "Required command not found: {command}"),
    (subprocess.TimeoutExpired([], 10), "{command} timed out after 10 seconds."),
    (OSError("Permission denied"), "Could not execute {command}: Permission denied"),
])
def test_dns_functions_propagate_command_execution_errors(
    function, command_name, error, message, subprocess_run,
):
    # Keep the real run_command to check that execution errors reach the caller.
    subprocess_run.side_effect = error

    with pytest.raises(CommandError) as caught:
        function("example.com")

    assert str(caught.value) == message.format(command=command_name)
    assert caught.value.__cause__ is error
    subprocess_run.assert_called_once()


@pytest.mark.parametrize("function, command_name", [
    (dns.dns_run, "getent"),
    (dns.ipv_check, "getent"),
    (dns.dns_query, "getent"),
    (dns.dns_latency, "dig"),
])
def test_dns_functions_propagate_unexpected_returncode(function, command_name, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], 1, "", "  command failed\n",
    )

    with pytest.raises(CommandError) as caught:
        function("example.com")

    assert str(caught.value) == f"{command_name} failed: command failed"


def test_ipv_check_propagates_error_from_second_address_family(monkeypatch):
    output = (FIXTURE_DIRECTORY / "getent_ahostsv4.txt").read_text(encoding="utf-8")
    error = CommandError("getent timed out after 10 seconds.")
    command = Mock(side_effect=[CommandResult([], output, "", 0, 0.0), error])
    monkeypatch.setattr(dns, "run_command", command)

    with pytest.raises(CommandError) as caught:
        dns.ipv_check("example.com")

    assert caught.value is error
    assert command.call_args_list == [
        call(["getent", "ahostsv4", "example.com"], allowed_codes=(0, 2)),
        call(["getent", "ahostsv6", "example.com"], allowed_codes=(0, 2)),
    ]


@pytest.mark.parametrize("code, expected_status, resolved, address", [
    (0, "OK", True, "2001:db8::10"),
    (2, "FAILED", False, None),
])
def test_dns_query_integrates_shared_runner_and_accepts_not_found(
    code, expected_status, resolved, address, subprocess_run,
):
    raw = (FIXTURE_DIRECTORY / "getent_hosts.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], code, raw, "")

    result = dns.dns_query("example.com")

    assert result == DnsQueryResult(
        resolved=resolved,
        hostname="example.com",
        ip_address=address,
        execution_status=ExecutionStatus.COMPLETED,
    )
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["getent", "hosts", "example.com"]


def test_ipv_check_integrates_both_families_with_shared_runner(subprocess_run):
    ipv4 = (FIXTURE_DIRECTORY / "getent_ahostsv4.txt").read_text(encoding="utf-8")
    ipv6 = (FIXTURE_DIRECTORY / "getent_ahostsv6.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = [
        subprocess.CompletedProcess([], 0, ipv4, ""),
        subprocess.CompletedProcess([], 0, ipv6, ""),
    ]

    result = dns.ipv_check("example.com")

    assert result == DnsAddressResult(
        hostname="example.com",
        ipv4_addresses=["192.0.2.10", "192.0.2.20"],
        ipv6_addresses=["2001:db8::10", "2001:db8::20"],
        execution_status=ExecutionStatus.COMPLETED,
    )
    assert [item.args[0] for item in subprocess_run.call_args_list] == [
        ["getent", "ahostsv4", "example.com"], ["getent", "ahostsv6", "example.com"],
    ]


@pytest.mark.parametrize("fixture_name, status, latency, resolved", [
    ("dig_noerror.txt", "NOERROR", 12.0, True),
    ("dig_nxdomain.txt", "NXDOMAIN", 4.0, False),
    ("dig_servfail.txt", "SERVFAIL", 8.0, False),
])
def test_dns_latency_integrates_shared_runner(
    fixture_name, status, latency, resolved, subprocess_run,
):
    raw = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = dns.dns_latency("example.com")

    assert result == DnsLatencyResult(
        dns_latency_ms=latency,
        dns_status=status,
        resolved=resolved,
        hostname="example.com",
        execution_status=ExecutionStatus.COMPLETED,
        answer_count=2 if status == "NOERROR" else 0,
    )
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["dig", "+time=3", "+tries=1", "+stats", "-q", "example.com"]
    assert subprocess_run.call_args.kwargs["timeout"] == 10


INVALID_DNS_TARGETS = [
    "", "-f", "--help", "+trace", "+time=999", "@192.0.2.1", "999.1.2.3",
    "fe80::1%eth0", "example.com;id", "example.com\nid", "example.com\0", None,
]


@pytest.mark.parametrize("function", [dns.dns_run, dns.dns_query, dns.ipv_check, dns.dns_latency])
@pytest.mark.parametrize("site", INVALID_DNS_TARGETS)
def test_dns_functions_reject_invalid_targets_before_running_commands(function, site, subprocess_run):
    with pytest.raises(ValidationError):
        function(site)

    subprocess_run.assert_not_called()


@pytest.mark.parametrize("site", ["IN", "CH", "A", "localhost", "example.com.", "192.0.2.1", "2001:db8::1"])
def test_dns_latency_passes_valid_single_label_and_literal_names_as_query_name(site, subprocess_run):
    raw = (FIXTURE_DIRECTORY / "dig_noerror.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = dns.dns_latency(site)

    assert result.hostname == site
    assert result.execution_status is ExecutionStatus.COMPLETED
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["dig", "+time=3", "+tries=1", "+stats", "-q", site]


@pytest.mark.parametrize("site", ["IN", "localhost", "example.com."])
def test_dns_run_and_ipv_check_accept_valid_single_label_and_trailing_dot_names(site, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 2, "", "")

    query = dns.dns_query(site)
    addresses = dns.ipv_check(site)

    assert query.hostname == site
    assert addresses.hostname == site
    assert [item.args[0] for item in subprocess_run.call_args_list] == [
        ["getent", "hosts", site], ["getent", "ahostsv4", site], ["getent", "ahostsv6", site],
    ]
