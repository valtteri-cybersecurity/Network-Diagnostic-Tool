from network_diagnostic.core.models import (
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    ListeningSocket, NetworkInterface, PacketLossStatus, PingResult, ServiceInfo, SocketCounts,
)


def test_ping_result_preserves_measurement_fields_and_execution_status():
    result = PingResult(
        target="internet", execution_status=ExecutionStatus.COMPLETED,
        packets_sent=3, packets_received=2, packet_loss_percent=33.3333,
        min_rtt_ms=10.0, avg_rtt_ms=15.0, max_rtt_ms=20.0, mdev_ms=5.0,
        reachable=True, raw_output="ping output",
        packet_loss_status=PacketLossStatus.PARTIAL_LOSS,
        latency_analysis="OK", rtt_range_analysis="Good",
    )

    assert result.target == "internet"
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert (result.packets_sent, result.packets_received, result.packet_loss_percent) == (3, 2, 33.3333)
    assert (result.min_rtt_ms, result.avg_rtt_ms, result.max_rtt_ms, result.mdev_ms) == (10.0, 15.0, 20.0, 5.0)
    assert result.reachable is True
    assert result.raw_output == "ping output"
    assert result.packet_loss_status is PacketLossStatus.PARTIAL_LOSS
    assert result.latency_analysis == "OK"
    assert result.rtt_range_analysis == "Good"
    assert result.error is None


def test_ping_result_defaults_do_not_invent_measurements_or_reachability():
    result = PingResult(target="gateway")

    assert result.execution_status is ExecutionStatus.FAILED
    assert (result.packets_sent, result.packets_received, result.packet_loss_percent) == (None, None, None)
    assert (result.min_rtt_ms, result.avg_rtt_ms, result.max_rtt_ms, result.mdev_ms) == (None, None, None, None)
    assert result.reachable is None
    assert result.raw_output == ""
    assert result.packet_loss_status is PacketLossStatus.UNAVAILABLE
    assert result.latency_analysis is None
    assert result.rtt_range_analysis is None
    assert result.error is None


def test_ping_result_distinguishes_zero_measurements_from_missing_values():
    result = PingResult(
        packets_sent=3, packets_received=0, packet_loss_percent=100.0,
        min_rtt_ms=0.0, avg_rtt_ms=0.0, max_rtt_ms=0.0, mdev_ms=0.0,
        execution_status=ExecutionStatus.COMPLETED, reachable=False,
    )

    assert result.packets_received == 0
    assert result.reachable is False
    assert result.min_rtt_ms == 0.0
    assert result.avg_rtt_ms == 0.0
    assert result.max_rtt_ms == 0.0
    assert result.mdev_ms == 0.0


def test_timeout_result_keeps_partial_output_without_claiming_completion():
    result = PingResult(
        target="internet", execution_status=ExecutionStatus.TIMEOUT,
        raw_output="partial reply", error="ping timed out",
    )

    assert result.execution_status is ExecutionStatus.TIMEOUT
    assert result.raw_output == "partial reply"
    assert result.error == "ping timed out"
    assert result.reachable is None
    assert result.packets_received is None


def test_dns_models_keep_unknown_values_separate_from_negative_answers():
    query = DnsQueryResult(hostname="example.com")
    latency = DnsLatencyResult(hostname="example.com")
    negative = DnsLatencyResult(
        hostname="missing.example.com", execution_status=ExecutionStatus.COMPLETED,
        dns_status="NXDOMAIN", resolved=False, dns_latency_ms=0.0, answer_count=0,
    )

    assert query.hostname == "example.com"
    assert query.resolved is None
    assert query.ip_address is None
    assert query.error is None
    assert query.execution_status is ExecutionStatus.FAILED
    assert latency.dns_latency_ms is None
    assert latency.answer_count is None
    assert latency.dns_status is None
    assert latency.resolved is None
    assert latency.error is None
    assert negative.execution_status is ExecutionStatus.COMPLETED
    assert negative.resolved is False
    assert negative.dns_status == "NXDOMAIN"
    assert negative.answer_count == 0
    assert negative.dns_latency_ms == 0.0


def test_address_lists_are_independent_between_results_and_address_families():
    first = DnsAddressResult(hostname="first.example.com")
    second = DnsAddressResult(hostname="second.example.com")

    first.ipv4_addresses.append("192.0.2.10")
    first.ipv6_addresses.append("2001:db8::10")

    assert first.ipv4_addresses == ["192.0.2.10"]
    assert first.ipv6_addresses == ["2001:db8::10"]
    assert second.ipv4_addresses == []
    assert second.ipv6_addresses == []
    assert second.error is None


def test_listening_socket_preserves_wildcards_and_ipv6_address_text():
    result = ListeningSocket("tcp", "LISTEN", "[::]", "*", "*", "*")

    assert result.protocol == "tcp"
    assert result.state == "LISTEN"
    assert result.local_address == "[::]"
    assert result.local_port == "*"
    assert result.peer_address == "*"
    assert result.peer_port == "*"
    assert ListeningSocket().local_port is None


def test_socket_counts_and_service_info_keep_zero_and_missing_port_distinct():
    counts = SocketCounts()
    service = ServiceInfo(protocol="TCP", local_address="0.0.0.0", port=22, service_name="SSH")

    assert counts.tcp_count == 0
    assert counts.udp_count == 0
    assert service.protocol == "TCP"
    assert service.local_address == "0.0.0.0"
    assert service.port == 22
    assert service.service_name == "SSH"
    assert ServiceInfo().port is None


def test_network_interface_retains_connection_text_and_optional_fields():
    result = NetworkInterface("wlp2s0", "wifi", "connected", "Office: WiFi")

    assert result.device == "wlp2s0"
    assert result.interface_type == "wifi"
    assert result.state == "connected"
    assert result.connection == "Office: WiFi"
    assert NetworkInterface().connection is None
