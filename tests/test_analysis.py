from dataclasses import replace

import pytest

import analysis
from models import (
    DnsLatencyResult, LatencyAnalysis, ListeningSocket, PacketLossStatus,
    PingResult, ServiceInfo, SocketCounts,
)


def test_count_open_ports_counts_rows_and_normalizes_protocol_case():
    rows = [
        ListeningSocket(protocol="tcp", local_port="22"),
        ListeningSocket(protocol="TCP", local_port="22"),
        ListeningSocket(protocol="uDp", local_port="53"),
        ListeningSocket(protocol="sctp"),
        ListeningSocket(),
    ]

    result = analysis.count_open_ports(rows)

    assert result == SocketCounts(tcp_count=2, udp_count=1)


def test_count_open_ports_with_empty_input():
    result = analysis.count_open_ports([])

    assert result == SocketCounts(tcp_count=0, udp_count=0)


def test_analyze_open_ports_identifies_services_and_preserves_order():
    rows = [
        ListeningSocket(protocol="TCP", local_address="0.0.0.0", local_port="22"),
        ListeningSocket(protocol="UDP", local_address="127.0.0.53", local_port=53),
        ListeningSocket(protocol="TCP", local_address="[::]", local_port="65000"),
    ]

    result = analysis.analyze_open_ports(rows)

    assert result == [
        ServiceInfo(protocol="TCP", local_address="0.0.0.0", port=22, service_name="SSH"),
        ServiceInfo(
            protocol="UDP",
            local_address="127.0.0.53",
            port=53,
            service_name="Domain Name System (DNS)",
        ),
        ServiceInfo(protocol="TCP", local_address="[::]", port=65000, service_name="UNKNOWN"),
    ]


def test_analyze_open_ports_with_empty_input():
    result = analysis.analyze_open_ports([])

    assert result == []


@pytest.mark.parametrize("row", [
    ListeningSocket(),
    ListeningSocket(local_port=""),
    ListeningSocket(local_port="https"),
    ListeningSocket(local_port="22.5"),
    ListeningSocket(local_port="*"),
    ListeningSocket(local_port=None),
    ListeningSocket(local_port=[]),
])
def test_analyze_open_ports_skips_unconvertible_or_missing_ports(row):
    result = analysis.analyze_open_ports([row, ListeningSocket(local_port="443")])

    assert result == [
        ServiceInfo(protocol="UNKNOWN", local_address="UNKNOWN", port=443, service_name="HTTPS"),
    ]


@pytest.mark.parametrize("protocol", ["TCP", "UDP", "sctp", ""])
def test_analyze_open_ports_preserves_protocol_and_uses_port_only(protocol):
    rows = [ListeningSocket(protocol=protocol, local_port="22")]

    result = analysis.analyze_open_ports(rows)

    assert result == [
        ServiceInfo(protocol=protocol, local_address="UNKNOWN", port=22, service_name="SSH"),
    ]


@pytest.mark.parametrize("port", ["0", "65535", "-1", "65536"])
def test_analyze_open_ports_currently_accepts_any_integer_port(port):
    # Current behavior: even out-of-range integers are accepted.
    result = analysis.analyze_open_ports([ListeningSocket(local_port=port)])

    assert result == [
        ServiceInfo(
            protocol="UNKNOWN",
            local_address="UNKNOWN",
            port=int(port),
            service_name="UNKNOWN",
        ),
    ]


def test_analyze_open_ports_currently_truncates_float_port():
    result = analysis.analyze_open_ports([ListeningSocket(local_port=22.9)])

    assert result == [
        ServiceInfo(protocol="UNKNOWN", local_address="UNKNOWN", port=22, service_name="SSH"),
    ]


@pytest.mark.parametrize("latency, expected_status", [
    (0, "EXCELLENT"),
    (19.999, "EXCELLENT"),
    (20, "GOOD"),
    (20.001, "GOOD"),
    (49.999, "GOOD"),
    (50, "SLOW"),
    (50.001, "SLOW"),
    (1000, "SLOW"),
])
def test_dns_latency_analyzation_thresholds(latency, expected_status):
    data = DnsLatencyResult(dns_latency_ms=latency)

    result = analysis.dns_latency_analyzation(data)

    assert result == LatencyAnalysis(latency_ms=latency, status=expected_status)


def test_dns_latency_analyzation_with_missing_measurement():
    with pytest.raises(TypeError):
        analysis.dns_latency_analyzation(DnsLatencyResult())


@pytest.mark.parametrize("latency", [None, "20"])
def test_dns_latency_analyzation_with_non_numeric_measurement(latency):
    with pytest.raises(TypeError):
        analysis.dns_latency_analyzation(DnsLatencyResult(dns_latency_ms=latency))


@pytest.mark.parametrize("latency, expected_status", [
    (0, "Excellent"),
    (0.999, "Excellent"),
    (1, "Good"),
    (1.001, "Good"),
    (9.999, "Good"),
    (10, "OK"),
    (10.001, "OK"),
    (29.999, "OK"),
    (30, "Warning"),
    (30.001, "Warning"),
    (59.999, "Warning"),
    (60, "Poor"),
    (60.001, "Poor"),
    (1000, "Poor"),
])
def test_ping_latency_analyzation_thresholds(latency, expected_status):
    result = analysis.ping_latency_analyzation(PingResult(avg_rtt_ms=latency))

    assert result == expected_status


def test_ping_latency_analyzation_with_missing_average():
    with pytest.raises(TypeError):
        analysis.ping_latency_analyzation(PingResult())


@pytest.mark.parametrize("latency", [None, "10"])
def test_ping_latency_analyzation_with_non_numeric_average(latency):
    with pytest.raises(TypeError):
        analysis.ping_latency_analyzation(PingResult(avg_rtt_ms=latency))


@pytest.mark.parametrize("minimum, maximum, expected_status", [
    (10, 10, "Excellent"),
    (10, 14.999, "Excellent"),
    (10, 15, "Good"),
    (10, 15.001, "Good"),
    (10, 29.999, "Good"),
    (10, 30, "Warning"),
    (10, 30.001, "Warning"),
    (10, 59.999, "Warning"),
    (10, 60, "Unstable"),
    (10, 60.001, "Unstable"),
    (10, 1010, "Unstable"),
])
def test_analyze_jitter_uses_rtt_range(minimum, maximum, expected_status):
    result = analysis.analyze_jitter(minimum, maximum)

    assert result == expected_status


@pytest.mark.parametrize("minimum, maximum", [(None, 10), (0, None), ("0", 10)])
def test_analyze_jitter_with_non_numeric_measurements(minimum, maximum):
    with pytest.raises(TypeError):
        analysis.analyze_jitter(minimum, maximum)


def test_analyze_jitter_currently_accepts_reversed_measurements():
    result = analysis.analyze_jitter(20, 10)

    assert result == "Excellent"


def test_get_open_ports_filters_states_and_normalizes_case():
    rows = [
        ListeningSocket(protocol="tCp", state="listen", local_address="0.0.0.0", local_port="22"),
        ListeningSocket(protocol="uDp", state="unconn", local_address="[::]", local_port="53"),
        ListeningSocket(protocol="TCP", state="ESTAB"),
        ListeningSocket(protocol="TCP", state="UNCONN"),
        ListeningSocket(protocol="UDP", state="LISTEN"),
        ListeningSocket(protocol="sctp", state="LISTEN"),
        ListeningSocket(state="LISTEN"),
        ListeningSocket(protocol="tcp"),
        ListeningSocket(),
    ]

    result = analysis.get_open_ports(rows)

    assert result == [
        ListeningSocket(protocol="TCP", state="LISTEN", local_address="0.0.0.0", local_port="22"),
        ListeningSocket(protocol="UDP", state="UNCONN", local_address="[::]", local_port="53"),
    ]


def test_get_open_ports_preserves_missing_address_and_port():
    result = analysis.get_open_ports([ListeningSocket(protocol="tcp", state="LISTEN")])

    assert result == [
        ListeningSocket(
            protocol="TCP",
            state="LISTEN",
            local_address=None,
            local_port=None,
        ),
    ]


def test_get_open_ports_with_empty_input():
    result = analysis.get_open_ports([])

    assert result == []


def test_get_open_ports_preserves_duplicate_socket_rows():
    row = ListeningSocket(protocol="TCP", state="LISTEN", local_address="0.0.0.0", local_port="22")

    result = analysis.get_open_ports([row, replace(row)])

    assert result == [row, row]


def test_get_known_ports_contains_service_suggestions():
    result = analysis.get_known_ports()

    assert result[22] == "SSH"
    assert result[53] == "Domain Name System (DNS)"
    assert result[80] == "HTTP"
    assert result[443] == "HTTPS"
    assert result[3306] == "MySQL / MariaDB"
    assert result[51820] == "WireGuard (common default)"
    assert 65000 not in result
    assert all(isinstance(port, int) and 1 <= port <= 65535 for port in result)
    assert all(isinstance(service, str) and service for service in result.values())


def test_get_known_ports_returns_an_independent_dictionary():
    first = analysis.get_known_ports()
    first[22] = "Changed"
    first[65000] = "Custom"

    second = analysis.get_known_ports()

    assert second[22] == "SSH"
    assert 65000 not in second


def test_latency_classifiers_currently_accept_negative_measurements():
    dns_result = analysis.dns_latency_analyzation(DnsLatencyResult(dns_latency_ms=-1))
    ping_result = analysis.ping_latency_analyzation(PingResult(avg_rtt_ms=-1))

    assert dns_result == LatencyAnalysis(latency_ms=-1, status="EXCELLENT")
    assert ping_result == "Excellent"


def test_get_open_ports_keeps_peer_information_and_wildcard_ports():
    socket = ListeningSocket(
        protocol="udp",
        state="UNCONN",
        local_address="*",
        local_port="*",
        peer_address="*",
        peer_port="*",
    )

    result = analysis.get_open_ports([socket])

    assert result == [replace(socket, protocol="UDP")]
    assert analysis.count_open_ports(result) == SocketCounts(tcp_count=0, udp_count=1)
    assert analysis.analyze_open_ports(result) == []


@pytest.mark.parametrize("loss, expected", [
    (None, PacketLossStatus.UNAVAILABLE),
    (0, PacketLossStatus.NO_LOSS),
    (0.0001, PacketLossStatus.PARTIAL_LOSS),
    (33.3333, PacketLossStatus.PARTIAL_LOSS),
    (99.9999, PacketLossStatus.PARTIAL_LOSS),
    (100, PacketLossStatus.TOTAL_LOSS),
    (-1, PacketLossStatus.INVALID),
    (101, PacketLossStatus.INVALID),
    (float("nan"), PacketLossStatus.INVALID),
])
def test_analyze_packet_loss_preserves_existing_classification_rules(loss, expected):
    result = analysis.analyze_packet_loss(loss)

    assert result is expected
