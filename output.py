import os
from datetime import datetime
from typing import Optional

from models import (
    DnsAddressResult, DnsLatencyResult, DnsQueryResult, ExecutionStatus,
    LatencyAnalysis, ListeningSocket, NetworkInterface, PacketLossStatus, PingResult, ServiceInfo, SocketCounts,
)


MENU_WIDTH = 60
MENU_INDENT = "    "


def clear_screen():
    os.system("clear")


def display_menu(title, options):
    print()
    print(title)
    print("-" * MENU_WIDTH)

    for number, option in enumerate(options, start=1):
        print(f"{MENU_INDENT}{number}. {option}")

    print("-" * MENU_WIDTH)
    print()


def mainmenu1(network_connected):
    date = datetime.now().strftime("%d-%m-%Y")

    if network_connected is None:
        status = "NetworkManager status unavailable"
    elif network_connected:
        status = "Connected interface detected"
    else:
        status = "No connected interface detected"

    print()
    print(f"Date   : {date}")
    print(f"Status : {status}")

    display_menu(
        "NETWORK DIAGNOSTIC TOOL",
        [
            "Ping diagnostics",
            "DNS diagnostics",
            "Port analysis",
            "System snapshot",
            "Exit program"
        ]
    )


def mainmenu2():
    display_menu(
        "PING DIAGNOSTICS",
        [
            "Localhost ping",
            "Gateway ping",
            "Internet ping",
            "Internet DNS ping",
            "Back to main menu"
        ]
    )


def mainmenu3():
    display_menu(
        "DNS DIAGNOSTICS",
        [
            "Query test",
            "Latency test",
            "IPv4 / IPv6 address test",
            "Back to main menu"
        ]
    )


def mainmenu4():
    display_menu(
        "PORT ANALYSIS",
        [
            "List listening sockets",
            "Show common service names by port",
            "Back to main menu"
        ]
    )


def mainmenu5():
    display_menu(
        "SYSTEM SNAPSHOT",
        [
            "Routing table overview",
            "Network interface status",
            "Back to main menu"
        ]
    )


def display_menu_prompt(max_choice):
    print(f"Choose action 1-{max_choice}: ", end="", flush=True)


def display_menu_error(max_choice):
    print(f"Invalid selection. Enter a number from 1 to {max_choice}.")
    print()


def display_command_error(message):
    print()
    print(f"Error: {message}")
    print()


def display_exit_message():
    print()
    print("Program closed.")


def dns_output_header(title):
    print()
    print(title)
    print("-" * len(title))
    print()


def display_ping_result(result: PingResult) -> None:
    if result.raw_output:
        print(result.raw_output)
        print()
    if result.error is not None:
        print(f"Error: {result.error}")


def display_ping_report(result: PingResult) -> None:
    display_ping_result(result)
    explanations = {
        PacketLossStatus.UNAVAILABLE: "Packet loss information is unavailable.",
        PacketLossStatus.TOTAL_LOSS: (
            "No test packets were received. The target did not respond "
            "or ICMP traffic is blocked."
        ),
        PacketLossStatus.NO_LOSS: "All test packets were received.",
        PacketLossStatus.INVALID: "Invalid packet loss value.",
    }
    if result.error is not None:
        reason = result.error
    elif result.packet_loss_status is PacketLossStatus.TOTAL_LOSS and result.icmp_errors:
        reason = (
            f"No replies were received, but {result.icmp_errors} ICMP error messages were "
            "reported (for example, destination unreachable)."
        )
    elif result.packet_loss_status is PacketLossStatus.PARTIAL_LOSS:
        reason = (
            f"{result.packet_loss_percent:g}% of test packets were lost. "
            "Packet loss may indicate a connection problem."
        )
    else:
        reason = explanations[result.packet_loss_status]

    title = f"{result.target.upper()} PING REPORT"
    print(title)
    print("-" * len(title))

    fields = (
        ("status", result.execution_status.value), ("reason", reason),
        ("sent", result.packets_sent), ("received", result.packets_received),
        ("loss", result.packet_loss_percent), ("icmp errors", result.icmp_errors),
        ("min", result.min_rtt_ms),
        ("avg", result.avg_rtt_ms), ("max", result.max_rtt_ms),
        ("mdev", result.mdev_ms), ("reachable", result.reachable),
    )
    for key, value in fields:
        if key in ("avg", "min", "max", "mdev") and value is not None:
            print(f"{key:<20}: {value} ms")
        elif key == "loss" and value is not None:
            print(f"{key:<20}: {value:g}%")
        else:
            print(f"{key:<20}: {value}")
    if result.error is not None:
        print(f"{'error':<20}: {result.error}")

    print()
    latency = result.latency_analysis if result.latency_analysis is not None else "Not available"
    rtt_range = result.rtt_range_analysis if result.rtt_range_analysis is not None else "Not available"
    print(f"{'Latency analysis':<20}: {latency}")
    print(f"{'RTT range analysis':<20}: {rtt_range}")
    print()


def dns_query_output(result: DnsQueryResult):
    dns_output_header("HOSTNAME RESOLUTION TEST")
    status = "UNKNOWN"
    if result.resolved is not None:
        status = "OK" if result.resolved else "FAILED"

    print(f"Hostname  : {result.hostname or 'Unknown'}")
    print(f"Execution : {result.execution_status.value}")
    print(f"Status    : {status}")
    print(f"IP address: {result.ip_address or 'No address found'}")
    if result.error is not None:
        print(f"Error     : {result.error}")
    print()


def dns_latency_output(result: DnsLatencyResult, analysis: Optional[LatencyAnalysis] = None):
    dns_output_header("DNS LATENCY TEST")
    latency = result.dns_latency_ms
    resolved = "Unknown" if result.resolved is None else ("Yes" if result.resolved else "No")
    print(f"Hostname  : {result.hostname or 'Unknown'}")
    print(f"Execution : {result.execution_status.value}")
    print(f"DNS status: {result.dns_status or 'UNKNOWN'}")
    print(f"Resolved  : {resolved}")
    if result.error is not None:
        print(f"Error     : {result.error}")

    if latency is None:
        print("Latency   : Measurement unavailable")
        print("Analysis  : DNS response time could not be measured")
        print()
        return

    status = analysis.status if analysis is not None else "UNKNOWN"
    descriptions = {
        "EXCELLENT": "Excellent - very fast DNS response",
        "GOOD": "Good - fast DNS response",
        "SLOW": "Slow - DNS response may need investigation",
    }
    if result.execution_status is ExecutionStatus.COMPLETED:
        print("Status    : Measurement completed")
    else:
        print("Status    : Measurement failed")
    print(f"Latency   : {latency:.2f} ms")
    print(f"Analysis  : {descriptions.get(status, status)}")
    print()


def ipv_check_output(result: DnsAddressResult):
    print(f"Hostname : {result.hostname or 'Unknown'}")
    print(f"Execution: {result.execution_status.value}")
    if result.ipv4_addresses:
        print("IPv4 addresses:")
        for address in result.ipv4_addresses:
            print(f"  - {address}")
    else:
        print("IPv4 addresses: None found")
    if result.ipv6_addresses:
        print("IPv6 addresses:")
        for address in result.ipv6_addresses:
            print(f"  - {address}")
    else:
        print("IPv6 addresses: None found")
    if result.error is not None:
        print(f"Error    : {result.error}")
    print()


def ipv_check_results_output(results: list[DnsAddressResult]):
    dns_output_header("RESOLVED IPv4 / IPv6 ADDRESSES")

    for result in results:
        ipv_check_output(result)


def display_open_ports_header():
    title = "LISTENING TCP / UNCONNECTED UDP SOCKETS"

    print(title)
    print("-" * len(title))
    print()


def display_open_ports_count(counts: SocketCounts):
    print(f"Total {counts.tcp_count} listening TCP sockets")
    print(f"Total {counts.udp_count} unconnected UDP sockets")
    print()


def display_open_ports(open_port_data: list[ListeningSocket]):
    if not open_port_data:
        print("No matching TCP or UDP sockets found.")
        print()
        return

    protocol_width = 10
    state_width = 12
    address_width = 40
    port_width = 8

    header = (
        f"{'PROTOCOL':<{protocol_width}}"
        f"{'STATE':<{state_width}}"
        f"{'LOCAL ADDRESS':<{address_width}}"
        f"{'PORT':>{port_width}}"
    )

    print(header)
    print("-" * len(header))

    for item in open_port_data:
        protocol = item.protocol if item.protocol is not None else "UNKNOWN"
        state = item.state if item.state is not None else "UNKNOWN"
        address = item.local_address if item.local_address is not None else "UNKNOWN"
        port = item.local_port if item.local_port is not None else "UNKNOWN"

        print(
            f"{protocol:<{protocol_width}}"
            f"{state:<{state_width}}"
            f"{address:<{address_width}}"
            f"{port:>{port_width}}"
        )

    print()


def display_known_services(services: list[ServiceInfo]):
    title = "COMMON SERVICE NAMES BY PORT"

    print(title)
    print("-" * len(title))
    print()

    if not services:
        print("No matching TCP or UDP sockets found.")
        print()
        return

    protocol_width = 10
    address_width = 40
    port_width = 8

    header = (
        f"{'PROTOCOL':<{protocol_width}}"
        f"{'LOCAL ADDRESS':<{address_width}}"
        f"{'PORT':>{port_width}}  "
        f"SERVICE"
    )

    print(header)
    print("-" * len(header))

    for item in services:
        protocol = item.protocol if item.protocol is not None else "UNKNOWN"
        address = item.local_address if item.local_address is not None else "UNKNOWN"
        port = item.port if item.port is not None else "UNKNOWN"
        service = item.service_name if item.service_name is not None else "UNKNOWN"

        print(
            f"{protocol:<{protocol_width}}"
            f"{address:<{address_width}}"
            f"{port:>{port_width}}  "
            f"{service}"
        )

    print()


def display_routing_table(
    hostname,
    interface,
    ip_address,
    gateway,
    route_data
):
    title = "ROUTING TABLE OVERVIEW"

    print(title)
    print("-" * len(title))
    print()

    print("Hostname :", hostname or "Unavailable")
    print("Interface:", interface or "Unavailable")
    print("IP       :", ip_address or "Unavailable")
    print("Gateway  :", gateway or "Unavailable")
    print()
    print("IPv4 routes:")

    if not route_data:
        print("No route information available.")
    else:
        for line in route_data:
            print(line)

    print()


def display_network_interface_status(interface_data: list[NetworkInterface]):
    title = "NETWORK INTERFACE STATUS"

    print()
    print(title)
    print("-" * len(title))
    print()

    if not interface_data:
        print("No network interface information available.")
        return

    for item in interface_data:
        print(f"Device    : {item.device if item.device is not None else 'UNKNOWN'}")
        print(f"Type      : {item.interface_type if item.interface_type is not None else 'UNKNOWN'}")
        print(f"State     : {item.state if item.state is not None else 'UNKNOWN'}")
        print(f"Connection: {item.connection or '--'}")
        print()
