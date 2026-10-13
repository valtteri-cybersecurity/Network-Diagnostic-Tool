import os
from datetime import datetime


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


def display_ping_result(result, report):
    print(result)
    print()

    if result.startswith("PING_ERROR:"):
        return result.removeprefix("PING_ERROR:").strip()

    loss = report.get("loss")

    if loss is None:
        return "Packet loss information is unavailable."

    if loss == 100:
        return (
            "No test packets were received. The target did not respond "
            "or ICMP traffic is blocked."
        )

    if loss == 0:
        return "All test packets were received."

    if 0 < loss < 100:
        return (
            f"{loss:g}% of test packets were lost. "
            "Packet loss may indicate a connection problem."
        )

    return "Invalid packet loss value."


def display_ping_report(report, target, jitter, latency):
    title = f"{target.upper()} PING REPORT"

    print(title)
    print("-" * len(title))

    for key, value in report.items():
        if key in ("avg", "min", "max") and value is not None:
            print(f"{key:<20}: {value} ms")
        elif key == "loss" and value is not None:
            print(f"{key:<20}: {value:g}%")
        else:
            print(f"{key:<20}: {value}")

    print()
    print(f"{'Latency analysis':<20}: {latency}")
    print(f"{'RTT range analysis':<20}: {jitter}")
    print()


def dns_query_output(result):
    dns_output_header("HOSTNAME RESOLUTION TEST")

    site = result.get("site", "Unknown")
    status = result.get("status", "UNKNOWN")
    ip = result.get("ip")

    print(f"Hostname  : {site}")
    print(f"Status    : {status}")
    print(f"IP address: {ip if ip else 'No address found'}")
    print()


def dns_latency_output(result):
    dns_output_header("DNS LATENCY TEST")

    site = result.get("site", "Unknown")
    latency = result.get("dns_latency_ms")

    print(f"Hostname  : {site}")
    print(f"DNS status: {result.get('dns_status', 'UNKNOWN')}")
    print(f"Resolved  : {'Yes' if result.get('resolved') else 'No'}")

    if latency is None:
        print("Latency   : Measurement unavailable")
        print("Analysis  : DNS response time could not be measured")
        print()
        return

    analysis_result = result.get("analysis", {})
    status = analysis_result.get("status", "UNKNOWN")

    descriptions = {
        "EXCELLENT": "Excellent - very fast DNS response",
        "GOOD": "Good - fast DNS response",
        "SLOW": "Slow - DNS response may need investigation"
    }

    print("Status    : Measurement completed")
    print(f"Latency   : {latency:.2f} ms")
    print(f"Analysis  : {descriptions.get(status, status)}")
    print()


def ipv_check_output(result):
    site = result.get("site", "Unknown")
    ipv4 = result.get("ipv4", [])
    ipv6 = result.get("ipv6", [])

    print(f"Hostname : {site}")

    if ipv4:
        print("IPv4 addresses:")
        for address in ipv4:
            print(f"  - {address}")
    else:
        print("IPv4 addresses: None found")

    if ipv6:
        print("IPv6 addresses:")
        for address in ipv6:
            print(f"  - {address}")
    else:
        print("IPv6 addresses: None found")

    print()


def ipv_check_results_output(results):
    dns_output_header("RESOLVED IPv4 / IPv6 ADDRESSES")

    for result in results:
        ipv_check_output(result)


def display_open_ports_header():
    title = "LISTENING TCP / UNCONNECTED UDP SOCKETS"

    print(title)
    print("-" * len(title))
    print()


def display_open_ports_count(counts):
    print(f"Total {counts['tcp']} listening TCP sockets")
    print(f"Total {counts['udp']} unconnected UDP sockets")
    print()


def display_open_ports(open_port_data):
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
        protocol = item.get("protocol", "UNKNOWN")
        state = item.get("state", "UNKNOWN")
        address = item.get("local_address", "UNKNOWN")
        port = item.get("local_port", "UNKNOWN")

        print(
            f"{protocol:<{protocol_width}}"
            f"{state:<{state_width}}"
            f"{address:<{address_width}}"
            f"{port:>{port_width}}"
        )

    print()


def display_known_services(services):
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
        protocol = item.get("protocol", "UNKNOWN")
        address = item.get("local_address", "UNKNOWN")
        port = item.get("port", "UNKNOWN")
        service = item.get("service", "UNKNOWN")

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


def display_network_interface_status(interface_data):
    title = "NETWORK INTERFACE STATUS"

    print()
    print(title)
    print("-" * len(title))
    print()

    if not interface_data:
        print("No network interface information available.")
        return

    for item in interface_data:
        print(f"Device    : {item.get('device', 'UNKNOWN')}")
        print(f"Type      : {item.get('type', 'UNKNOWN')}")
        print(f"State     : {item.get('state', 'UNKNOWN')}")
        print(f"Connection: {item.get('connection') or '--'}")
        print()
