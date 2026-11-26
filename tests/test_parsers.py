from pathlib import Path

import pytest

from network_diagnostic.core import parsers
from network_diagnostic.core.exceptions import ParsingError
from network_diagnostic.core.models import (
    DnsLatencyResult, ExecutionStatus, ListeningSocket, NetworkInterface, PingResult,
)


FIXTURES = Path(__file__).parent / "fixtures"
PING_FIXTURES = FIXTURES / "ping"
DNS_FIXTURES = FIXTURES / "dns"
NETWORK_FIXTURES = FIXTURES / "network"
SUMMARY = "3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n"
RTT = "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041 ms\n"


def parsing_error(message):
    return pytest.raises(ParsingError, match="^" + __import__("re").escape(message) + "$")


# --- parse_port -------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [("0", 0), ("1", 1), ("22", 22), ("65535", 65535), ("00022", 22)])
def test_parse_port_accepts_decimal_ports_in_range(text, expected):
    assert parsers.parse_port(text) == expected


@pytest.mark.parametrize("text", [
    "", "65536", "99999", "123456", "-1", "+22", " 22", "22 ", "22.0", "0x16", "*",
    "\u0662\u0662", "22\n", 22, 22.0, None, True,
])
def test_parse_port_rejects_invalid_values(text):
    with parsing_error("Invalid port number"):
        parsers.parse_port(text)


# --- parse_ping_statistics --------------------------------------------------


@pytest.mark.parametrize("fixture_name, received, loss, average, maximum, deviation", [
    ("success.txt", 3, 0.0, 12.5, 15.0, 2.041),
    ("partial_loss.txt", 2, 33.3333, 15.0, 20.0, 5.0),
])
def test_parse_ping_statistics_reads_successful_measurements(fixture_name, received, loss, average, maximum, deviation):
    output = (PING_FIXTURES / fixture_name).read_text(encoding="utf-8")

    result = parsers.parse_ping_statistics(output, "example.com")

    assert result == PingResult(
        target="example.com", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=received, packet_loss_percent=loss,
        min_rtt_ms=10.0, avg_rtt_ms=average, max_rtt_ms=maximum, mdev_ms=deviation,
        reachable=True, raw_output=output,
    )


def test_parse_ping_statistics_accepts_round_trip_statistics():
    output = (PING_FIXTURES / "success.txt").read_text(encoding="utf-8")
    output = output.replace("rtt min/avg/max/mdev", "round-trip min/avg/max/stddev")

    result = parsers.parse_ping_statistics(output, "example.com")

    assert result == PingResult(
        target="example.com", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=3, packet_loss_percent=0.0,
        min_rtt_ms=10.0, avg_rtt_ms=12.5, max_rtt_ms=15.0, mdev_ms=2.041,
        reachable=True, raw_output=output,
    )


def test_parse_ping_statistics_reports_total_loss_as_completed_measurement_without_rtt():
    output = (PING_FIXTURES / "total_loss.txt").read_text(encoding="utf-8")

    result = parsers.parse_ping_statistics(output, "internet")

    assert result == PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=0, packet_loss_percent=100.0,
        reachable=False, raw_output=output, icmp_errors=3,
    )
    # Missing RTT values stay None; they are not zero.
    assert result.min_rtt_ms is None
    assert result.avg_rtt_ms is None
    assert result.max_rtt_ms is None
    assert result.mdev_ms is None


def test_parse_ping_statistics_preserves_zero_rtt_values():
    output = SUMMARY + "rtt min/avg/max/mdev = 0.000/0.000/0.000/0.000 ms\n"

    result = parsers.parse_ping_statistics(output, "localhost")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.reachable is True
    assert (result.min_rtt_ms, result.avg_rtt_ms, result.max_rtt_ms, result.mdev_ms) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("rtt_line, values", [
    ("rtt min/avg/max/mdev = 10.000/10.000/10.000/0.000 ms", (10.0, 10.0, 10.0, 0.0)),
    ("rtt min/avg/max/mdev = 10/12.5/15/2 ms", (10.0, 12.5, 15.0, 2.0)),
])
def test_parse_ping_statistics_accepts_equal_and_integer_rtt_values(rtt_line, values):
    result = parsers.parse_ping_statistics(SUMMARY + rtt_line, "internet")

    assert (result.min_rtt_ms, result.avg_rtt_ms, result.max_rtt_ms, result.mdev_ms) == values


@pytest.mark.parametrize("summary, received, loss", [
    ("3 packets transmitted, 2 received, 33.3333% packet loss", 2, 33.3333),
    ("3 packets transmitted, 1 received, 66.6667% packet loss", 1, 66.6667),
    ("3 packets transmitted, 2 received, 33.3% packet loss", 2, 33.3),
    ("3 packets transmitted, 2 packets received, 33% packet loss", 2, 33.0),
    ("3 packets transmitted, 1 packets received, 66% packet loss", 1, 66.0),
    ("7 packets transmitted, 6 received, 14.2857% packet loss", 6, 14.2857),
    ("3 packets transmitted, 3 received, +2 duplicates, 0% packet loss", 3, 0.0),
    ("3 packets transmitted, 2 received, +1 errors, 33.3333% packet loss, time 2003ms", 2, 33.3333),
])
def test_parse_ping_statistics_accepts_rounded_loss_duplicates_and_errors(summary, received, loss):
    result = parsers.parse_ping_statistics(summary + "\n" + RTT, "internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.packets_received == received
    assert result.packet_loss_percent == loss


@pytest.mark.parametrize("output", [
    "", " \n", "PING example.com (192.0.2.10) 56(84) bytes of data.\n", RTT,
])
def test_parse_ping_statistics_rejects_missing_packet_statistics(output):
    with parsing_error("No packet statistics found"):
        parsers.parse_ping_statistics(output, "internet")


@pytest.mark.parametrize("output", [
    "3 packets transmitted, 3 received", "3 packets transmitted, 0% packet loss", "3 packets transmitted",
    "3 packets transmitted, , 0% packet loss",
])
def test_parse_ping_statistics_rejects_incomplete_packet_statistics(output):
    with parsing_error("Incomplete packet statistics"):
        parsers.parse_ping_statistics(output, "internet")


@pytest.mark.parametrize("output", [
    "invalid packets transmitted, 3 received, 0% packet loss",
    "3.5 packets transmitted, 3 received, 0% packet loss",
    "3 packets transmitted, invalid received, 0% packet loss",
    "3 packets transmitted, 3 received, invalid% packet loss",
    "3 packets transmitted, 3 received, 0 packet loss",
    "3 packets transmitted, 3 received, 1e1% packet loss",
    "3 packets transmitted, 3 received, inf% packet loss",
    "-3 packets transmitted, 3 received, 0% packet loss",
    "\u0663 packets transmitted, 3 received, 0% packet loss",
])
def test_parse_ping_statistics_rejects_invalid_packet_numbers(output):
    with parsing_error("Unable to parse packet statistics"):
        parsers.parse_ping_statistics(output, "internet")


@pytest.mark.parametrize("summary, error", [
    ("0 packets transmitted, 0 received, 0% packet loss", "Invalid packet statistics: no packets transmitted"),
    ("3 packets transmitted, 4 received, 0% packet loss",
     "Invalid packet statistics: more packets received than transmitted"),
    ("3 packets transmitted, 3 received, 100.1% packet loss",
     "Invalid packet statistics: packet loss outside 0-100%"),
    ("3 packets transmitted, 3 received, 1% packet loss",
     "Invalid packet statistics: packet loss does not match packet counts"),
    ("3 packets transmitted, 2 received, 34.5% packet loss",
     "Invalid packet statistics: packet loss does not match packet counts"),
    ("3 packets transmitted, 0 received, 0% packet loss",
     "Invalid packet statistics: packet loss does not match packet counts"),
])
def test_parse_ping_statistics_rejects_contradictory_packet_statistics(summary, error):
    with parsing_error(error):
        parsers.parse_ping_statistics(summary + "\n" + RTT, "internet")


def test_parse_ping_statistics_requires_rtt_after_received_packets():
    with parsing_error("No latency data available"):
        parsers.parse_ping_statistics(SUMMARY, "internet")


@pytest.mark.parametrize("rtt_line", [
    "rtt min/avg/max/mdev 10.000/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/2.041/99 ms",
    "rtt min/avg/max/mdev = invalid/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/invalid/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/invalid/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/invalid ms",
    "rtt min/avg/max/mdev = ",
    "rtt min/avg/max/mdev = -1.000/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/12.500/15.000/-2.041 ms",
    "rtt min/avg/max/mdev = nan/nan/nan/nan ms",
    "rtt min/avg/max/mdev = 10.000/inf/inf/2.041 ms",
    "rtt min/avg/max/mdev = 1e1/1e1/1e1/0 ms",
    "rtt min/avg/max/mdev = 10,000/12,500/15,000/2,041 ms",
    "rtt min/avg/max/mdev = 10.000//15.000/2.041 ms",
])
def test_parse_ping_statistics_rejects_malformed_or_invalid_rtt(rtt_line):
    with parsing_error("Unable to parse latency statistics"):
        parsers.parse_ping_statistics(SUMMARY + rtt_line, "internet")


@pytest.mark.parametrize("rtt_line", [
    "rtt min/avg/max/mdev = 20.000/12.500/10.000/2.041 ms",
    "rtt min/avg/max/mdev = 13.000/12.500/15.000/2.041 ms",
    "rtt min/avg/max/mdev = 10.000/15.001/15.000/2.041 ms",
])
def test_parse_ping_statistics_rejects_inconsistent_rtt_order(rtt_line):
    with parsing_error("Invalid latency statistics: expected min <= avg <= max"):
        parsers.parse_ping_statistics(SUMMARY + rtt_line, "internet")


def test_parse_ping_statistics_rejects_rtt_without_received_packets():
    output = "3 packets transmitted, 0 received, 100% packet loss\n" + RTT

    with parsing_error("Invalid latency statistics: latency reported without received packets"):
        parsers.parse_ping_statistics(output, "internet")


def test_parse_ping_statistics_reports_malformed_rtt_before_reachability_conflict():
    output = "3 packets transmitted, 0 received, 100% packet loss\nrtt invalid\n"

    with parsing_error("Unable to parse latency statistics"):
        parsers.parse_ping_statistics(output, "internet")


@pytest.mark.parametrize("output, error", [
    (SUMMARY + SUMMARY + RTT, "Multiple packet statistics found"),
    (SUMMARY + RTT + RTT, "Multiple latency statistics found"),
])
def test_parse_ping_statistics_rejects_repeated_statistics(output, error):
    with parsing_error(error):
        parsers.parse_ping_statistics(output, "internet")


def test_parse_ping_statistics_ignores_target_names_that_contain_summary_words():
    output = (
        "PING transmitted.received.example (192.0.2.10) 56(84) bytes of data.\n"
        "64 bytes from transmitted.received.example (192.0.2.10): icmp_seq=1 ttl=64 time=10.0 ms\n"
        "\n--- transmitted.received.example ping statistics ---\n" + SUMMARY + RTT
    )

    result = parsers.parse_ping_statistics(output, "internet")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.packets_sent == 3


def test_parse_ping_statistics_is_deterministic_and_does_not_modify_input():
    output = (PING_FIXTURES / "success.txt").read_text(encoding="utf-8")
    original = str(output)

    first = parsers.parse_ping_statistics(output, "internet")
    second = parsers.parse_ping_statistics(output, "internet")

    assert first == second
    assert first is not second
    assert output == original


# --- DNS parsers ------------------------------------------------------------


@pytest.mark.parametrize("output, expected", [
    ("192.0.2.10      example.com\n", "192.0.2.10"),
    ("\n2001:db8::10    example.com\n192.0.2.10 example.com\n", "2001:db8::10"),
    ("192.0.2.10 example.com alias.example.com\n192.0.2.20 example.com\n", "192.0.2.10"),
    ("", None),
    (" \n\t\n", None),
])
def test_parse_getent_hosts_returns_first_valid_address_or_none(output, expected):
    assert parsers.parse_getent_hosts(output) == expected


def test_parse_getent_hosts_reads_fixture():
    output = (DNS_FIXTURES / "getent_hosts.txt").read_text(encoding="utf-8")

    assert parsers.parse_getent_hosts(output) == "2001:db8::10"


@pytest.mark.parametrize("output", [
    "not-an-address example.com\n",
    "192.0.2.10 example.com\n999.1.2.3 example.com\n",
    "2001:db8::zz example.com\n",
    "0x7f.0.0.1 example.com\n",
    "\u0661\u0669\u0662.0.2.1 example.com\n",
])
def test_parse_getent_hosts_rejects_invalid_addresses(output):
    with parsing_error("Invalid address in getent output"):
        parsers.parse_getent_hosts(output)


def test_parse_getent_addresses_filters_family_invalid_and_duplicate_lines():
    ipv4 = (DNS_FIXTURES / "getent_ahostsv4.txt").read_text(encoding="utf-8")
    ipv6 = (DNS_FIXTURES / "getent_ahostsv6.txt").read_text(encoding="utf-8")
    noisy = "\ninvalid STREAM\n999.1.2.3 DGRAM\n2001:db8::10 STREAM\n192.0.2.10 STREAM\n192.0.2.10 RAW\n"

    assert parsers.parse_getent_addresses(ipv4, 4) == ["192.0.2.10", "192.0.2.20"]
    assert parsers.parse_getent_addresses(ipv6, 6) == ["2001:db8::10", "2001:db8::20"]
    assert parsers.parse_getent_addresses(noisy, 4) == ["192.0.2.10"]
    assert parsers.parse_getent_addresses(noisy, 6) == ["2001:db8::10"]
    assert parsers.parse_getent_addresses("", 4) == []


@pytest.mark.parametrize("fixture_name, status, latency, answers, resolved", [
    ("dig_noerror.txt", "NOERROR", 12.0, 2, True),
    ("dig_nxdomain.txt", "NXDOMAIN", 4.0, 0, False),
    ("dig_servfail.txt", "SERVFAIL", 8.0, 0, False),
])
def test_parse_dig_output_keeps_response_code_separate_from_query_time(
    fixture_name, status, latency, answers, resolved,
):
    output = (DNS_FIXTURES / fixture_name).read_text(encoding="utf-8")

    result = parsers.parse_dig_output(output, "example.com")

    assert result == DnsLatencyResult(
        hostname="example.com", execution_status=ExecutionStatus.COMPLETED,
        dns_status=status, resolved=resolved, dns_latency_ms=latency, answer_count=answers,
    )


@pytest.mark.parametrize("old_text, new_text, latency, answers, resolved", [
    (";; Query time: 12 msec", "", None, 2, True),
    ("ANSWER: 2", "RECORDS: 2", 12.0, None, None),
    ("ANSWER: 2", "ANSWER: 0", 12.0, 0, False),
    ("Query time: 12 msec", "Query time: 0 msec", 0.0, 2, True),
    ("Query time: 12 msec", "Query time: 0.125 msec", 0.125, 2, True),
])
def test_parse_dig_output_keeps_missing_values_as_none_and_zero_as_zero(old_text, new_text, latency, answers, resolved):
    output = (DNS_FIXTURES / "dig_noerror.txt").read_text(encoding="utf-8").replace(old_text, new_text)

    result = parsers.parse_dig_output(output, "example.com")

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.dns_latency_ms == latency
    assert result.answer_count == answers
    assert result.resolved is resolved


def test_parse_dig_output_fast_nxdomain_is_not_resolution():
    output = (DNS_FIXTURES / "dig_nxdomain.txt").read_text(encoding="utf-8").replace("Query time: 4 msec", "Query time: 0 msec")

    result = parsers.parse_dig_output(output, "missing.example.com")

    assert result.dns_latency_ms == 0.0
    assert result.dns_status == "NXDOMAIN"
    assert result.resolved is False


@pytest.mark.parametrize("output", ["", " \n", "unrecognized dig output\n", ";; Query time: 12 msec\n"])
def test_parse_dig_output_requires_response_status(output):
    with parsing_error("Unable to parse DNS response status"):
        parsers.parse_dig_output(output, "example.com")


@pytest.mark.parametrize("old_text, new_text, error", [
    ("status: NOERROR", "status: noerror", "Invalid DNS response status"),
    ("status: NOERROR", "status: ", "Invalid DNS response status"),
    ("status: NOERROR", "status: 1NOERROR", "Invalid DNS response status"),
    ("ANSWER: 2", "ANSWER: invalid", "Invalid DNS answer count"),
    ("ANSWER: 2", "ANSWER: -1", "Invalid DNS answer count"),
    ("ANSWER: 2", "ANSWER: 2.5", "Invalid DNS answer count"),
    ("Query time: 12 msec", "Query time: -5 msec", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: nan msec", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: inf msec", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: 1e3 msec", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: 12 sec", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: 12", "Invalid DNS query time"),
    ("Query time: 12 msec", "Query time: 12 msec extra", "Invalid DNS query time"),
])
def test_parse_dig_output_rejects_invalid_field_values(old_text, new_text, error):
    output = (DNS_FIXTURES / "dig_noerror.txt").read_text(encoding="utf-8").replace(old_text, new_text)

    with parsing_error(error):
        parsers.parse_dig_output(output, "example.com")


@pytest.mark.parametrize("extra, error", [
    (";; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 2\n", "Conflicting DNS response status values"),
    (";; flags: qr; QUERY: 1, ANSWER: 3, AUTHORITY: 0\n", "Conflicting DNS answer count values"),
    (";; Query time: 13 msec\n", "Conflicting DNS query time values"),
])
def test_parse_dig_output_rejects_conflicting_repeated_fields(extra, error):
    output = (DNS_FIXTURES / "dig_noerror.txt").read_text(encoding="utf-8") + extra

    with parsing_error(error):
        parsers.parse_dig_output(output, "example.com")


def test_parse_dig_output_accepts_identical_repeated_fields():
    output = (DNS_FIXTURES / "dig_noerror.txt").read_text(encoding="utf-8")

    result = parsers.parse_dig_output(output + ";; Query time: 12 msec\n", "example.com")

    assert result.dns_latency_ms == 12.0


# --- Route and address parsers ----------------------------------------------


def test_route_parsers_read_fixture():
    routes = (NETWORK_FIXTURES / "ip_route.txt").read_text(encoding="utf-8").splitlines()

    assert parsers.parse_default_gateway(routes) == "192.0.2.1"
    assert parsers.parse_default_interface(routes) == "enp3s0"
    assert parsers.parse_default_source(routes) is None


@pytest.mark.parametrize("routes", [[], [""], ["invalid"], ["192.0.2.0/24 dev enp3s0 src 192.0.2.10"],
                                    ["default via"], ["default dev"], ["default src"]])
def test_route_parsers_return_none_for_missing_values(routes):
    assert parsers.parse_default_gateway(routes) is None
    assert parsers.parse_default_interface(routes) is None
    assert parsers.parse_default_source(routes) is None


@pytest.mark.parametrize("parser, routes, error", [
    (parsers.parse_default_gateway, ["default via -f dev enp3s0"],
     "Invalid default gateway: Invalid IPv4 address: '-f'"),
    (parsers.parse_default_gateway, ["default via 0x7f.0.0.1 dev enp3s0"],
     "Invalid default gateway: Invalid IPv4 address: '0x7f.0.0.1'"),
    (parsers.parse_default_gateway, ["default via 2001:db8::1 dev enp3s0"],
     "Invalid default gateway: Invalid IPv4 address: '2001:db8::1'"),
    (parsers.parse_default_interface, ["default via 192.0.2.1 dev " + "a" * 16],
     "Invalid default route interface: Invalid interface name: '" + "a" * 16 + "'"),
    (parsers.parse_default_interface, ["default via 192.0.2.1 dev eth0;id"],
     "Invalid default route interface: Invalid interface name: 'eth0;id'"),
    (parsers.parse_default_source, ["default via 192.0.2.1 dev enp3s0 src 192.0.2.256"],
     "Invalid default route source address: Invalid IPv4 address: '192.0.2.256'"),
])
def test_route_parsers_reject_invalid_values(parser, routes, error):
    with parsing_error(error):
        parser(routes)


@pytest.mark.parametrize("output, expected", [
    ("2: enp3s0    inet 192.0.2.10/24 brd 192.0.2.255 scope global enp3s0\n", "192.0.2.10"),
    ("2: enp3s0 inet\n2: enp3s0 inet 192.0.2.11/24 scope global enp3s0\n", "192.0.2.11"),
    ("2: enp3s0 inet6 2001:db8::10/64 scope global\n", None),
    ("", None),
])
def test_parse_interface_ipv4_address_reads_first_address(output, expected):
    assert parsers.parse_interface_ipv4_address(output) == expected


def test_parse_interface_ipv4_address_reads_fixture():
    output = (NETWORK_FIXTURES / "ip_address.txt").read_text(encoding="utf-8")

    assert parsers.parse_interface_ipv4_address(output) == "192.0.2.10"


def test_parse_interface_ipv4_address_rejects_invalid_address():
    with parsing_error("Invalid interface address: Invalid IPv4 address: 'not-an-address'"):
        parsers.parse_interface_ipv4_address("2: enp3s0 inet not-an-address/24 scope global\n")


# --- nmcli and ss parsers ---------------------------------------------------


@pytest.mark.parametrize("output, expected", [
    ("lo:connected\nenp4s0:disconnected\nwlp2s0:connected\n",
     [("lo", "connected"), ("enp4s0", "disconnected"), ("wlp2s0", "connected")]),
    ("enp3s0:connected (externally)\n", [("enp3s0", "connected (externally)")]),
    ("\n \nwl\\:an:connected\n", [("wl:an", "connected")]),
    ("", []),
])
def test_parse_nmcli_states_reads_device_state_pairs(output, expected):
    assert parsers.parse_nmcli_states(output) == expected


def test_parse_nmcli_output_accepts_empty_type_and_connection():
    lines = ["enp4s0::disconnected:"]

    result = parsers.parse_nmcli_output(lines)

    assert result == [
        NetworkInterface(device="enp4s0", interface_type="", state="disconnected", connection=""),
    ]


@pytest.mark.parametrize("line, expected", [
    ("enp3s0:ethernet:connected:Wired connection 1",
     ["enp3s0", "ethernet", "connected", "Wired connection 1"]),
    (r"wlp2s0:wifi:connected:Office\: WiFi\\Guest",
     ["wlp2s0", "wifi", "connected", "Office: WiFi\\Guest"]),
    ("enp3s0:ethernet:disconnected:", ["enp3s0", "ethernet", "disconnected", ""]),
    (":::", ["", "", "", ""]),
    ("", [""]),
    ("trailing\\", ["trailing\\"]),
    (r"name\\:state", ["name\\", "state"]),
])
def test_split_nmcli_fields_handles_escaped_and_empty_fields(line, expected):
    result = parsers.split_nmcli_fields(line)

    assert result == expected


def test_parse_nmcli_output_reads_devices_and_escaped_connections():
    lines = (NETWORK_FIXTURES / "nmcli_devices.txt").read_text(encoding="utf-8").splitlines()

    result = parsers.parse_nmcli_output(lines)

    assert result == [
        NetworkInterface(
            device="enp3s0",
            interface_type="ethernet",
            state="connected",
            connection="Wired connection 1",
        ),
        NetworkInterface(
            device="wlp2s0",
            interface_type="wifi",
            state="connected",
            connection="Office: WiFi\\Guest",
        ),
        NetworkInterface(
            device="lo",
            interface_type="loopback",
            state="connected (externally)",
            connection="lo",
        ),
        NetworkInterface(
            device="enp4s0",
            interface_type="ethernet",
            state="disconnected",
            connection="",
        ),
    ]


def test_parse_nmcli_output_skips_empty_lines_and_keeps_valid_ones():
    lines = ["", "  ", "lo:loopback:unmanaged:", ""]

    result = parsers.parse_nmcli_output(lines)

    assert result == [
        NetworkInterface(device="lo", interface_type="loopback", state="unmanaged", connection=""),
    ]


def test_parse_nmcli_output_with_empty_input():
    result = parsers.parse_nmcli_output([])

    assert result == []


def test_parse_ss_output_reads_ipv4_ipv6_and_wildcard_sockets():
    lines = (NETWORK_FIXTURES / "ss_sockets.txt").read_text(encoding="utf-8").splitlines()

    result = parsers.parse_ss_output(lines)

    assert result == [
        ListeningSocket(
            protocol="udp",
            state="UNCONN",
            local_address="127.0.0.53%lo",
            local_port="53",
            peer_address="0.0.0.0",
            peer_port="*",
        ),
        ListeningSocket(
            protocol="udp",
            state="UNCONN",
            local_address="[fe80::1%enp3s0]",
            local_port="546",
            peer_address="[::]",
            peer_port="*",
        ),
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="0.0.0.0",
            local_port="22",
            peer_address="0.0.0.0",
            peer_port="*",
        ),
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="[::]",
            local_port="22",
            peer_address="[::]",
            peer_port="*",
        ),
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="[2001:db8::10]",
            local_port="443",
            peer_address="[::]",
            peer_port="*",
        ),
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="*",
            local_port="8080",
            peer_address="*",
            peer_port="*",
        ),
    ]


def test_parse_ss_output_skips_header_and_empty_lines_and_ignores_process_column():
    lines = [
        "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port",
        "", "   ",
        'tcp LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:(("python",pid=1234,fd=3))',
    ]

    result = parsers.parse_ss_output(lines)

    assert result == [
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="127.0.0.1",
            local_port="8080",
            peer_address="0.0.0.0",
            peer_port="*",
        ),
    ]


def test_parse_ss_output_with_empty_input():
    result = parsers.parse_ss_output([])

    assert result == []


def test_parse_ss_output_keeps_unknown_protocol_with_valid_endpoints():
    # Unknown protocols are not parser errors; analysis filters them.
    result = parsers.parse_ss_output(["sctp LISTEN 0 128 192.0.2.10:9899 *:*"])

    assert result == [
        ListeningSocket(
            protocol="sctp",
            state="LISTEN",
            local_address="192.0.2.10",
            local_port="9899",
            peer_address="*",
            peer_port="*",
        ),
    ]


@pytest.mark.parametrize("line", [
    "sctp LISTEN 0 128 invalid:not-a-port *:*",
    "tcp LISTEN 0 128 0.0.0.0:65536 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:-1 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:+22 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:123456 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0: 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:http",
    "tcp LISTEN 0 128 not-an-address:22 0.0.0.0:*",
    "tcp LISTEN 0 128 999.1.2.3:22 0.0.0.0:*",
    "tcp LISTEN 0 128 [2001:db8::zz]:22 [::]:*",
    "tcp LISTEN 0 128 [::1%-f]:22 [::]:*",
    "tcp LISTEN 0 128 127.0.0.1%:22 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:22 999.1.2.3:*",
    "tcp LISTEN 0 128 \u0661.0.0.1:22 0.0.0.0:*",
    "tcp LISTEN 0 128 [::1:22 [::]:*",
    "tcp LISTEN 0 128 [::1]x:22 [::]:*",
    "tcp LISTEN 0 128 [fe80::1%eth0]%eth0:22 [::]:*",
    "tcp LISTEN 0 128 []:22 [::]:*",
])
def test_parse_ss_output_rejects_invalid_ports_and_addresses(line):
    valid = "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"

    with parsing_error("Unrecognized line in ss output"):
        parsers.parse_ss_output([line, valid])


@pytest.mark.parametrize("endpoint, address, port", [
    ("0.0.0.0:0", "0.0.0.0", "0"),
    ("0.0.0.0:65535", "0.0.0.0", "65535"),
    ("[::ffff:127.0.0.1]:631", "[::ffff:127.0.0.1]", "631"),
    ("[::1]:631", "[::1]", "631"),
    ("*%lo:53", "*%lo", "53"),
    ("0.0.0.0%wlp2s0:68", "0.0.0.0%wlp2s0", "68"),
    ("[fe80::1]%enp3s0:546", "[fe80::1]%enp3s0", "546"),
    ("[::]%enp3s0:546", "[::]%enp3s0", "546"),
])
def test_parse_ss_output_accepts_port_limits_and_address_forms(endpoint, address, port):
    result = parsers.parse_ss_output([f"udp UNCONN 0 0 {endpoint} *:*"])

    assert result == [
        ListeningSocket(
            protocol="udp", state="UNCONN", local_address=address, local_port=port,
            peer_address="*", peer_port="*",
        ),
    ]


def test_parse_ss_output_handles_unbracketed_ipv6():
    result = parsers.parse_ss_output(["tcp LISTEN 0 128 2001:db8::10:443 :::*"])

    assert result == [
        ListeningSocket(
            protocol="tcp",
            state="LISTEN",
            local_address="2001:db8::10",
            local_port="443",
            peer_address="::",
            peer_port="*",
        ),
    ]


def test_parse_ss_output_preserves_wildcard_local_port():
    lines = ["udp UNCONN 0 0 *:* *:*"]

    result = parsers.parse_ss_output(lines)

    assert result == [ListeningSocket(
        protocol="udp",
        state="UNCONN",
        local_address="*",
        local_port="*",
        peer_address="*",
        peer_port="*",
    )]


# --- Phase 10 regressions -----------------------------------------------------


def test_route_parsers_read_all_values_from_lowest_metric_default_route():
    routes = [
        "default via 198.51.100.1 dev wlan0 proto dhcp src 198.51.100.10 metric 600",
        "default via 192.0.2.1 dev enp3s0 proto dhcp metric 100",
    ]

    assert parsers.parse_default_gateway(routes) == "192.0.2.1"
    assert parsers.parse_default_interface(routes) == "enp3s0"
    assert parsers.parse_default_source(routes) is None


@pytest.mark.parametrize("routes, expected", [
    (["default via 192.0.2.1 dev enp3s0 metric 100", "default dev ppp0"], "ppp0"),
    (["default via 192.0.2.1 dev enp3s0", "default via 198.51.100.1 dev wlan0"], "enp3s0"),
    (["default via 192.0.2.1 dev enp3s0 metric 5", "default via 198.51.100.1 dev wlan0 metric 5"], "enp3s0"),
    (["default dev", "default via 192.0.2.1 dev enp3s0 metric 100"], "enp3s0"),
    (["192.0.2.0/24 dev eth9 metric 0", "unreachable default metric 0",
      "default via 192.0.2.1 dev enp3s0 metric 100"], "enp3s0"),
])
def test_select_default_route_uses_lowest_metric_and_first_on_tie(routes, expected):
    route = parsers.select_default_route(routes)

    assert route[route.index("dev") + 1] == expected


@pytest.mark.parametrize("routes", [[], [""], ["192.0.2.0/24 dev enp3s0"], ["default via"],
                                    ["default via 192.0.2.1 metric"]])
def test_select_default_route_returns_none_without_complete_default_route(routes):
    assert parsers.select_default_route(routes) is None


@pytest.mark.parametrize("metric", ["-1", "1e3", "0x10", "4294967296", "fast"])
def test_select_default_route_rejects_invalid_metric(metric):
    with parsing_error("Invalid default route metric"):
        parsers.select_default_route([f"default via 192.0.2.1 dev enp3s0 metric {metric}"])


def test_parse_getent_addresses_skips_ipv4_mapped_ipv6_addresses():
    # getent ahostsv6 uses AI_V4MAPPED, so an IPv4-only name returns mapped addresses.
    output = "::ffff:192.0.2.10 STREAM example.com\n::ffff:192.0.2.10 DGRAM\n2001:db8::10 STREAM\n"

    assert parsers.parse_getent_addresses(output, 6) == ["2001:db8::10"]
    assert parsers.parse_getent_addresses("::ffff:192.0.2.10 STREAM example.com\n", 6) == []


def test_parse_ping_statistics_reports_icmp_error_count():
    output = (PING_FIXTURES / "total_loss.txt").read_text(encoding="utf-8")

    result = parsers.parse_ping_statistics(output, "gateway")

    assert result.icmp_errors == 3
    assert result.reachable is False
    assert result.execution_status is ExecutionStatus.COMPLETED


def test_parse_ping_statistics_keeps_unreported_icmp_errors_as_none():
    result = parsers.parse_ping_statistics(SUMMARY + RTT, "internet")

    assert result.icmp_errors is None


@pytest.mark.parametrize("errors", ["+x", "3", "+-1"])
def test_parse_ping_statistics_rejects_invalid_icmp_error_count(errors):
    summary = f"3 packets transmitted, 0 received, {errors} errors, 100% packet loss\n"

    with parsing_error("Unable to parse packet statistics"):
        parsers.parse_ping_statistics(summary, "internet")


@pytest.mark.parametrize("line", [
    "tcp LISTEN 0 128",
    "tcp LISTEN 0 128 missing-port 0.0.0.0:*",
    "tcp LISTEN 0 128 0.0.0.0:22 missing-port",
])
def test_parse_ss_output_rejects_unrecognized_lines_instead_of_hiding_them(line):
    valid = "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*"

    with parsing_error("Unrecognized line in ss output"):
        parsers.parse_ss_output([valid, line])


# --- Phase 13 regressions: default route selection --------------------------


def test_select_default_route_skips_dead_route_with_lower_metric():
    # The kernel never uses a next hop marked dead, whatever its metric.
    routes = [
        "default via 198.51.100.1 dev wlan0 proto static metric 50 dead linkdown",
        "default via 192.0.2.1 dev enp3s0 proto dhcp metric 100",
    ]

    route = parsers.select_default_route(routes)

    assert route[route.index("dev") + 1] == "enp3s0"
    assert parsers.parse_default_gateway(routes) == "192.0.2.1"


def test_select_default_route_returns_none_when_only_dead_default_routes_exist():
    assert parsers.select_default_route(["default via 192.0.2.1 dev enp3s0 metric 100 dead"]) is None


@pytest.mark.parametrize("routes", [
    # `ip -4 route` prints the next hops of a multipath route on indented lines.
    ["default proto static metric 100",
     "\tnexthop via 192.0.2.1 dev enp3s0 weight 1",
     "\tnexthop via 198.51.100.1 dev wlan0 weight 1"],
    # A route that uses a nexthop group object.
    ["default nhid 100 proto static metric 100"],
    # Lines without an interface. iproute2 always prints `dev` for a single-path
    # unicast route; these inputs previously returned the gateway or None.
    ["default"], ["default via 192.0.2.1"],
])
def test_route_parsers_reject_default_route_without_single_next_hop(routes):
    message = "Unsupported default route: no single output interface (for example, a multipath route)"
    for parser in (parsers.select_default_route, parsers.parse_default_gateway,
                   parsers.parse_default_interface, parsers.parse_default_source):
        with parsing_error(message):
            parser(routes)


def test_select_default_route_ignores_multipath_route_with_higher_metric():
    routes = [
        "default via 192.0.2.1 dev enp3s0 metric 100",
        "default proto static metric 200",
        "\tnexthop via 198.51.100.1 dev wlan0 weight 1",
    ]

    assert parsers.parse_default_interface(routes) == "enp3s0"


# --- Phase 13 regressions: nmcli parsers reject unrecognized lines ----------


@pytest.mark.parametrize("line", [
    "invalid", "enp3s0:ethernet:connected", "enp3s0:ethernet:connected:extra:field",
    ":ethernet:connected:Wired", "enp3s0:ethernet::Wired",
])
def test_parse_nmcli_output_rejects_unrecognized_line_instead_of_partial_list(line):
    lines = ["lo:loopback:unmanaged:", line]

    with parsing_error("Unrecognized line in nmcli output"):
        parsers.parse_nmcli_output(lines)


@pytest.mark.parametrize("output", [
    "invalid\nwlp2s0:connected\n", "wlp2s0:connected:extra\n", ":connected\n", "enp3s0:\n",
])
def test_parse_nmcli_states_rejects_unrecognized_line(output):
    with parsing_error("Unrecognized line in nmcli output"):
        parsers.parse_nmcli_states(output)
