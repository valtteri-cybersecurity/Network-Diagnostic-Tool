from typing import Optional

from models import (
    DnsLatencyResult, LatencyAnalysis, ListeningSocket, PacketLossStatus, PingResult, ServiceInfo, SocketCounts,
)


def count_open_ports(open_port_data: list[ListeningSocket]) -> SocketCounts:
    tcp_count = 0
    udp_count = 0

    for row in open_port_data:
        protocol = (row.protocol or "").upper()

        if protocol == "TCP":
            tcp_count += 1

        elif protocol == "UDP":
            udp_count += 1

    return SocketCounts(tcp_count=tcp_count, udp_count=udp_count)


def analyze_open_ports(open_port_data: list[ListeningSocket]) -> list[ServiceInfo]:
    known_ports = get_known_ports()
    services = []

    for row in open_port_data:
        try:
            port = int(row.local_port)
        except (ValueError, TypeError):
            continue

        service_name = known_ports.get(port, "UNKNOWN")

        services.append(ServiceInfo(
            protocol=row.protocol if row.protocol is not None else "UNKNOWN",
            local_address=row.local_address if row.local_address is not None else "UNKNOWN",
            port=port, service_name=service_name,
        ))

    return services


def dns_latency_analyzation(latency_data: DnsLatencyResult) -> LatencyAnalysis:
    latency = latency_data.dns_latency_ms

    if latency < 20:
        status = "EXCELLENT"
    elif latency < 50:
        status = "GOOD"
    else:
        status = "SLOW"

    return LatencyAnalysis(latency_ms=latency, status=status)


def ping_latency_analyzation(result: PingResult) -> str:
    average_latency = result.avg_rtt_ms

    if average_latency < 1:
        return "Excellent"
    elif average_latency < 10:
        return "Good"
    elif average_latency < 30:
        return "OK"
    elif average_latency < 60:
        return "Warning"
    else:
        return "Poor"


def analyze_packet_loss(loss: Optional[float]) -> PacketLossStatus:
    if loss is None:
        return PacketLossStatus.UNAVAILABLE
    if loss == 100:
        return PacketLossStatus.TOTAL_LOSS
    if loss == 0:
        return PacketLossStatus.NO_LOSS
    if 0 < loss < 100:
        return PacketLossStatus.PARTIAL_LOSS
    return PacketLossStatus.INVALID


def analyze_jitter(minimum, maximum):
    rtt_range = maximum - minimum

    if rtt_range < 5:
        return "Excellent"
    elif rtt_range < 20:
        return "Good"
    elif rtt_range < 50:
        return "Warning"
    else:
        return "Unstable"


def get_open_ports(data: list[ListeningSocket]) -> list[ListeningSocket]:
    open_ports = []

    for row in data:
        protocol = (row.protocol or "").lower()
        state = (row.state or "").upper()

        is_listening_tcp = protocol == "tcp" and state == "LISTEN"
        is_listening_udp = protocol == "udp" and state == "UNCONN"

        if is_listening_tcp or is_listening_udp:
            open_ports.append(ListeningSocket(
                protocol=protocol.upper(), state=state,
                local_address=row.local_address, local_port=row.local_port,
                peer_address=row.peer_address, peer_port=row.peer_port,
            ))

    return open_ports


def get_known_ports():
    return {
        20: "FTP data",
        21: "FTP control",
        22: "SSH",
        23: "Telnet",
        3389: "Remote Desktop Protocol (RDP)",
        80: "HTTP",
        443: "HTTPS",
        8000: "Common development web server",
        8080: "Alternative HTTP",
        8443: "Alternative HTTPS",
        53: "Domain Name System (DNS)",
        853: "DNS over TLS (DoT)",
        784: "DNS over QUIC (legacy)",
        8853: "DNS over QUIC (legacy)",
        67: "DHCP server",
        68: "DHCP client",
        546: "DHCPv6 client",
        547: "DHCPv6 server",
        25: "SMTP",
        465: "SMTPS",
        587: "SMTP submission",
        110: "POP3",
        143: "IMAP",
        993: "IMAPS",
        995: "POP3S",
        1433: "Microsoft SQL Server",
        1521: "Oracle Database",
        3306: "MySQL / MariaDB",
        33060: "MySQL X Protocol",
        5432: "PostgreSQL",
        6379: "Redis",
        27017: "MongoDB",
        9200: "Elasticsearch HTTP",
        9300: "Elasticsearch transport",
        123: "Network Time Protocol (NTP)",
        161: "SNMP",
        162: "SNMP traps",
        179: "BGP",
        514: "Syslog (UDP) / shell (TCP)",
        520: "RIP",
        1812: "RADIUS authentication",
        1813: "RADIUS accounting",
        500: "IPsec IKE",
        4500: "IPsec NAT traversal",
        1194: "OpenVPN (common default)",
        51820: "WireGuard (common default)",
        69: "TFTP",
        111: "RPC portmapper",
        137: "NetBIOS name service",
        138: "NetBIOS datagram",
        139: "NetBIOS session",
        445: "SMB",
        2049: "NFS",
        3000: "Common application dashboard port",
        9090: "Prometheus (common default)",
        9100: "Node Exporter (common default)",
        515: "Line Printer Daemon (LPD)",
        631: "Internet Printing Protocol (IPP)",
        5900: "VNC",
        5901: "VNC display 1",
        5902: "VNC display 2",
        8006: "Proxmox VE Web UI (default)",
        88: "Kerberos",
        389: "LDAP",
        636: "LDAPS",
        1883: "MQTT",
        8883: "MQTT over TLS"
    }
