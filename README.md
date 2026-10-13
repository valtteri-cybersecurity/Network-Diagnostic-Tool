# Network Diagnostic Tool

A Python command-line tool for basic network troubleshooting on Linux. It combines ping tests, hostname resolution, DNS response-time measurements, local socket inspection, and network snapshots in one menu.

## Features

- Ping localhost, the default IPv4 gateway, 8.8.8.8, or google.com.
- Display packet counts, packet loss, and minimum, average, and maximum round-trip times.
- Classify average latency and the round-trip time range.
- Resolve google.com using the system hostname resolver.
- Measure DNS response time with dig and display the DNS response code.
- Look up IPv4 and IPv6 addresses separately for several predefined hostnames.
- List listening TCP sockets and unconnected UDP sockets.
- Suggest common service names based on local port numbers.
- Display the hostname, default-route interface, IPv4 address, gateway, and IPv4 routing table.
- Display NetworkManager interface states and connection names.

## Requirements

- Linux with Python 3.9 or newer.
- NetworkManager for interface status reporting.
- The following commands: ping, ip, ss, nmcli, getent, dig, hostname, and clear.

The project uses only the Python standard library. No third-party Python packages or pip installation are required.

On Ubuntu or Debian, install the command dependencies with:

```bash
sudo apt update
sudo apt install python3 iputils-ping iproute2 network-manager dnsutils libc-bin hostname ncurses-bin
```

Run the application as a normal user. The application itself does not require sudo.

## Usage

Open the project directory and run:

```bash
python3 main.py
```

Choose a numbered action from the menu:

1. Ping diagnostics
2. DNS diagnostics
3. Port analysis
4. System snapshot
5. Exit program

Each submenu includes a return option. Invalid selections prompt for another input. Press Ctrl+C to close the application.

The current version uses predefined targets. To change them, edit the target definitions in main.py and ping.py.

## Error handling

Network and DNS commands have a 10-second execution limit. Ping also has a 10-second limit. DNS queries made with dig use a three-second query timeout and one attempt, within the overall execution limit.

Missing commands, command failures, and timeouts are reported to the user. Network and DNS execution errors return to the main menu. If NetworkManager status is unavailable, the main menu remains accessible.

A hostname not found by getent is reported as an unsuccessful lookup. DNS response codes such as NXDOMAIN are displayed separately from response time: a fast response does not necessarily mean that the requested name exists.

## Project structure

| File | Responsibility |
| --- | --- |
| main.py | Menu navigation and coordination |
| network.py | Command execution, network data collection, and output parsing |
| ping.py | Ping execution, statistics parsing, and report coordination |
| dns.py | Hostname resolution, address lookups, and DNS measurements |
| analysis.py | Socket filtering, service-name suggestions, and measurement classifications |
| output.py | Menus, reports, error messages, and screen output |

All print calls are kept in output.py. Importing the application modules does not run diagnostic commands.

## Measurement limits

- Ping tests ICMP reachability to a specific target. A failed ping does not prove that the entire internet connection is down; ICMP may be blocked.
- Each ping test sends three packets. This small sample does not establish long-term connection stability.
- The RTT range is the maximum minus the minimum measured round-trip time. It is not a dedicated jitter measurement.
- Latency classifications are simple project-defined thresholds, not universal quality standards.
- getent uses the system name-resolution configuration, including sources such as /etc/hosts. Its results are not necessarily obtained through a DNS network query.
- IPv4 and IPv6 lookups show addresses returned by the system resolver. They do not test connectivity over either address family, and results can depend on host configuration.
- DNS latency measures one query to the configured resolver. Caching affects the result. The default dig lookup requests an A record.
- Socket counts count rows, not unique port numbers. One port may appear on several addresses.
- Service names are port-based suggestions. They do not identify the actual application or prove external accessibility.
- The routing overview uses the first default IPv4 route. VPNs, multiple default routes, and policy routing may require more detailed investigation.
- NetworkManager's connected state does not prove internet access.

## Validation

The current source has passed syntax and import checks and simulated checks for menu navigation, command errors, timeouts, packet-loss interpretation, socket parsing, NetworkManager output parsing, and DNS response handling.

Simulated checks do not establish compatibility with every Linux installation. Before relying on the tool, exercise each menu action on the target machine with the network connected and disconnected.
