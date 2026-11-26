# Network Diagnostic Tool

[![CI](https://github.com/valtteri-cybersecurity/Network-Diagnostic-Tool/actions/workflows/ci.yml/badge.svg)](https://github.com/valtteri-cybersecurity/Network-Diagnostic-Tool/actions/workflows/ci.yml)

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
- Collect all observations into one structured `DiagnosticSnapshot` and export it as
  JSON-compatible data (Python API, see [Diagnostic snapshot](#diagnostic-snapshot)).

## Requirements

- Linux with Python 3.11 or newer. Python 3.12 is the primary development version and the
  only version on which the test suite has been run locally (including the Docker development
  environment). The [CI pipeline](#continuous-integration) runs the tests on Python 3.11,
  3.12, 3.13, and 3.14.
- NetworkManager for interface status reporting.

The package uses only the Python standard library and has no runtime Python dependencies.
The diagnostics run the following Linux system tools, which are not Python packages and
are not installed by pip:

| Command | Package (Ubuntu/Debian) | Used for |
| --- | --- | --- |
| `ping` | iputils-ping | Ping diagnostics |
| `ip` | iproute2 | Routing table, interface, address, gateway |
| `ss` | iproute2 | Listening sockets |
| `nmcli` | network-manager | Connection status, interface states |
| `getent` | libc-bin | Hostname and address lookups |
| `dig` | dnsutils (bind9-dnsutils) | DNS response time |
| `hostname` | hostname | System snapshot |
| `clear` | ncurses-bin | Clearing the terminal (CLI only) |

On Ubuntu or Debian, install them from the distribution repositories with:

```bash
sudo apt update
sudo apt install python3 iputils-ping iproute2 network-manager dnsutils libc-bin hostname ncurses-bin
```

Run the application as a normal user. The application itself does not require sudo.

## Installation

Install the package into a virtual environment, never into the system Python
environment and never with `sudo pip`. From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Building the package downloads the build backend (setuptools) from PyPI unless it
is already available. The package is not published to PyPI.

## Usage

Start the command-line interface with the installed command:

```bash
network-diagnostic
```

or with the module form, which uses the same entry point:

```bash
python -m network_diagnostic
```

Both call `network_diagnostic.cli.main.run()`. The command is available only while
the virtual environment that contains the package is active (or by its full path,
e.g. `.venv/bin/network-diagnostic`). The former `python3 main.py` start-up has been
replaced by these commands; there is no `main.py` in the project root anymore.

Choose a numbered action from the menu:

1. Ping diagnostics
2. DNS diagnostics
3. Port analysis
4. System snapshot
5. Exit program

Each submenu includes a return option. Invalid selections prompt for another input. Press Ctrl+C to close the application.

The current version uses predefined targets. To change them, edit the target definitions in `src/network_diagnostic/diagnostic_service.py` (DNS hostnames) and `src/network_diagnostic/core/ping.py` (ping targets).

## Error handling

Network and DNS commands have a 10-second execution limit. Ping also has a 10-second limit; it waits at most two seconds for a reply when none has arrived (`-W 2`), so complete packet loss is reported as a completed measurement instead of a timeout. DNS queries made with dig use a three-second query timeout and one attempt, within the overall execution limit. When no DNS server replies (dig exit code 9), the DNS latency test returns a `timeout` result (`failed` for other no-reply cases such as a refused connection) with the error `No reply from DNS server`.

Missing commands, command failures, and timeouts are reported to the user. Command output is decoded as UTF-8; invalid bytes are replaced with U+FFFD instead of stopping the program. The CLI shows non-printable characters in system and command text (such as connection names, route lines, and command error output) as escapes, for example `\x1b`, so that terminal control sequences are not executed. Network and DNS execution errors return to the main menu. The system snapshot and the IPv4 / IPv6 address test instead show the parts that succeeded together with the errors of the parts that failed (see [Service layer](#service-layer)). If NetworkManager status is unavailable, the main menu remains accessible. The main menu status reflects only NetworkManager: "NetworkManager reports no connected interface" can also appear when another service, such as systemd-networkd, manages the connection.

Inputs that are rejected before a command runs raise `ValidationError`. The CLI reports them like other expected errors and returns to the main menu; ping reports them as a failed `PingResult`.

A hostname not found by getent is reported as an unsuccessful lookup. getent uses the same exit code when the name does not exist and when no resolver answered, so an unsuccessful lookup does not prove that the name does not exist. DNS response codes such as NXDOMAIN are displayed separately from response time: a fast response does not necessarily mean that the requested name exists.

## Project structure

The project is an installable Python package with a `src` layout:

```
pyproject.toml              Package metadata, build system, pytest and coverage settings
requirements-dev.txt        Test dependencies
src/network_diagnostic/
    __init__.py             Package marker (no import-time side effects)
    __main__.py             python -m network_diagnostic
    diagnostic_service.py   Application interface (service layer)
    core/                   Diagnostic engine
    cli/                    Command-line interface
tests/                      Tests and command-output fixtures
```

| Module (`network_diagnostic.`) | Responsibility |
| --- | --- |
| `cli.main` | Menu navigation, user choices, error display, and the `run()` entry point |
| `cli.output` | Menus, reports, error messages, and screen output |
| `diagnostic_service` | UI-independent application interface that coordinates the engine |
| `core.command_runner` | Safe command execution, timeouts, and execution timing |
| `core.exceptions` | Shared diagnostic, validation, parsing, and command exceptions |
| `core.validation` | Input validation before commands are executed |
| `core.parsers` | Pure, validating parsers for ping, DNS, and network command output |
| `core.models` | Typed command, diagnostic, socket, interface, and analysis data models |
| `core.network` | Network data collection |
| `core.ping` | UI-independent ping execution and analysis coordination |
| `core.dns` | Hostname resolution, address lookups, and DNS measurements |
| `core.analysis` | Socket filtering, service-name suggestions, and measurement classifications |
| `core.snapshot_serialization` | Allowlist-based conversion of a `DiagnosticSnapshot` to JSON-compatible data |

All imports are absolute package imports such as
`from network_diagnostic.core.models import PingResult`. Module names below refer to
these modules; `main.py` and `output.py` are in `cli/`, the engine modules in `core/`.

The diagnostic engine (`network_diagnostic.core`) consists of `ping`, `dns`, `network`, `analysis`, `parsers`,
`models`, `exceptions`, `validation`, `command_runner`, and `snapshot_serialization`. It executes commands, parses their output,
builds result models, and classifies measurements. It never prints, prompts,
clears the screen, or imports `main` or `output`. Importing modules runs no commands.

`main.py` owns input, navigation, diagnostic selection, and expected-error handling.
It calls only `diagnostic_service` and forwards the results to `output.py`, which only renders supplied data and
analyses. Output functions return no analysis data and do not modify result models
or run diagnostics. All print calls and terminal clearing remain in `output.py`.

Dependencies flow from CLI to service layer to engine: `main` calls
`diagnostic_service` and `output`; `diagnostic_service` calls `ping`, `dns`, `network`,
`parsers`, `analysis`, and `validation` but never `main`, `output`, or `subprocess`;
`output` uses only the shared models and standard-library formatting.
`ping` uses `network`, `analysis`, `parsers`, and the command runner; `dns` and `network`
use `parsers` and the runner; `analysis` uses models and `parsers.parse_port()`.
`ping`, `dns`, `network`,
`parsers`, and the runner use `validation`, which depends only on shared exceptions.
`parsers` uses models, shared exceptions, and `validation` only. The runner uses
models and shared exceptions. `models` uses shared exceptions and `validation` to check snapshot values;
`snapshot_serialization` uses only `models`.
There are no engine-to-service, engine-to-CLI, or circular dependencies. A future GUI
can call the same service functions and render the same results without CLI imports.

## Service layer

`network_diagnostic.diagnostic_service` is the application interface for the CLI,
a future GUI, and direct Python use. It consists of plain synchronous functions without
state, so a GUI can call them from a worker thread. They never print or prompt and return
dataclasses from `network_diagnostic.core.models`. Exceptions are defined in
`network_diagnostic.core.exceptions`.

```python
from network_diagnostic.diagnostic_service import run_ping_diagnostic
from network_diagnostic.core.models import ExecutionStatus

result = run_ping_diagnostic("internet")
if result.execution_status is ExecutionStatus.COMPLETED:
    print(result.packet_loss_percent, result.avg_rtt_ms)
else:
    print(result.error)
```

Importing the package or the service layer runs no commands and does not load the CLI.

| Function | Returns | Errors raised |
| --- | --- | --- |
| `get_connection_status()` | `bool`: a non-loopback device is connected | `CommandError`, `ParsingError` |
| `run_ping_diagnostic(target)` | `PingResult` for `localhost`, `gateway`, `internet`, or `google.com` | none: failures are `FAILED`/`TIMEOUT` results |
| `run_dns_query(hostname="google.com")` | `DnsQueryResult` | `ValidationError`, `CommandError` |
| `run_dns_latency_test(hostname="google.com")` | `DnsLatencyResult` with `latency_analysis` when a latency was measured | `ValidationError`, `CommandError` |
| `lookup_ip_addresses(hostnames=ADDRESS_LOOKUP_HOSTNAMES)` | `list[DnsAddressResult]` in input order | `ValidationError` (before any lookup) |
| `get_listening_ports()` | `ListeningPortReport(sockets, counts, services)` from one `ss` run | `CommandError`, `ParsingError` |
| `get_routing_snapshot()` | `RoutingSnapshot(hostname, interface, ip_address, gateway, routes, errors)` | none for command or parsing failures |
| `get_network_interfaces()` | `list[NetworkInterface]` | `CommandError`, `ParsingError` |
| `collect_diagnostic_snapshot(...)` | `DiagnosticSnapshot`, see [Diagnostic snapshot](#diagnostic-snapshot) | `ValidationError` (before any command) |

Error contract:

- Caller input is validated before any command runs; invalid input raises
  `ValidationError`. Ping keeps its contract of returning a failed `PingResult`.
- A function based on one diagnostic operation lets `CommandError` and
  `ParsingError` propagate. Measurements, including negative results, are returned.
- A function based on independent operations returns the parts that succeeded.
  `lookup_ip_addresses()` stores a failed lookup as a `DnsAddressResult` with
  status `FAILED` or `TIMEOUT` and an error, and continues with the other names.
  `get_routing_snapshot()` reads the routing table once; the hostname does not
  depend on it, while the interface, address, and gateway are read only when it is
  available. Failed parts stay `None` and their messages are stored in `errors`
  (keys `routes`, `hostname`, `interface`, `ip_address`, `gateway`). `routes=None`
  means the routing table is unavailable; `[]` means it has no routes.
- `get_listening_ports()`, `get_network_interfaces()`, and `get_connection_status()`
  are all-or-nothing: an unrecognized `ss` or `nmcli` line raises `ParsingError` so
  that an incomplete list is never shown as complete.
- An unsupported default route (no single output interface, such as a multipath
  route) is recorded as a `ParsingError` message in `RoutingSnapshot.errors` for
  `interface`, `ip_address`, and `gateway`, and as a failed gateway `PingResult`.
- Only `CommandError` and `ParsingError` are caught. Programming errors propagate.

## Diagnostic snapshot

A `DiagnosticSnapshot` (`network_diagnostic.core.models`) records the observations of
one collection run: routing, interfaces, listening sockets, pings, the DNS query, the
DNS response time, and address lookups. It holds measured observations only; it does
not classify problems or suggest repairs. A future Analysis Engine can refer to a
snapshot by `snapshot_id` and to each operation by its `operation_id`.

```python
from network_diagnostic.diagnostic_service import collect_diagnostic_snapshot
from network_diagnostic.core.snapshot_serialization import snapshot_to_dict

# Local collection only: no pings, no DNS lookups.
snapshot = collect_diagnostic_snapshot(
    ping_targets=(), address_hostnames=(),
    include_dns_query=False, include_dns_latency=False,
)
print(snapshot.routing.status, snapshot.interfaces.status)
data = snapshot_to_dict(snapshot)  # fresh dict of JSON-compatible values
```

`collect_diagnostic_snapshot()` takes keyword arguments only:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ping_targets` | `DEFAULT_PING_TARGETS` (`localhost`, `gateway`, `internet`, `google.com`) | Up to four unique ping aliases, in the order given |
| `dns_hostname` | `DEFAULT_DNS_HOSTNAME` (`google.com`) | Name for the DNS query and DNS response-time measurement |
| `address_hostnames` | `ADDRESS_LOOKUP_HOSTNAMES` | Up to four unique names for IPv4/IPv6 address lookups |
| `include_routing`, `include_interfaces`, `include_listening_sockets`, `include_dns_query`, `include_dns_latency` | `True` | Collect the section (must be a real `bool`) |

Targets are passed as a list or tuple; empty ones are allowed. The whole request is
validated (aliases, duplicates, counts, hostnames with the existing hostname validator,
flag types) and an invalid request raises `ValidationError` before any command runs.
Operations run one after another in a fixed order. On a platform other than Linux no
command runs.

The default request causes external network traffic: ICMP echo requests to `8.8.8.8`
(`internet`) and `google.com`, a `getent` and a `dig` query for `google.com`, and address
lookups for the four default names. The `gateway` ping reads the routing table again.
Empty target tuples and `include_dns_query=False`, `include_dns_latency=False` keep the
collection local.

Each section is a `CollectionResult` with `operation_id`, `source`, `target`, `status`,
`value`, `issues`, and per-operation `started_at`, `finished_at`, and
`duration_seconds`. Snapshot and operation timestamps are UTC wall-clock times;
durations come from a monotonic clock, so a wall-clock change never makes them negative.

| Status | Meaning | `value` | Timing |
| --- | --- | --- | --- |
| `COMPLETED` | Observation collected without collection errors | required | yes |
| `PARTIAL` | Usable observation, but identified fields failed (routing only) | required | yes |
| `FAILED` | The command, parsing, measurement, or result validation failed | `None` | yes |
| `TIMEOUT` | A command timed out | `None` | yes |
| `UNAVAILABLE` | A required command is not installed | `None` | yes |
| `UNSUPPORTED` | Not a Linux system; nothing was run | `None` | no |
| `NOT_COLLECTED` | The section was not requested | `None` | no |

Issues carry a controlled `code`, a fixed `message`, and for routing the affected
`field`. Error categories come only from exception types, never from error texts. When
a legacy function has already lost the exception type (ping, the routing parts, a
failed DNS result), the generic code `measurement_failed` is used; a missing `ping` or
`ip` command is therefore `FAILED`, not `UNAVAILABLE`. When the routing table cannot be
read but the hostname can, routing is `PARTIAL` with `measurement_failed` for `routes`
and `dependency_unavailable` for `interface`, `ip_address`, and `gateway`.

Negative network observations are collected results, not collection failures: total
packet loss, a name that is not resolved, NXDOMAIN or SERVFAIL from a received DNS
reply, 0 ms, empty lists, and a missing gateway or round-trip time are `COMPLETED`.

Every collected value is validated before it is stored (types, finite non-negative
numbers, loss 0–100, received ≤ sent, DNS answer count ≤ 65535, ports, IP syntax, the
ping alias and DNS name matching the operation target, IPv6 scopes as interface names,
no C0/C1 control characters, integers within 2**53 − 1).
A value that fails this check becomes `FAILED` with `invalid_result`. The snapshot holds
detached copies without legacy error texts, raw output, or analysis fields; it is a
mutable object owned by the caller, not tamper-proof evidence. `validate()` rechecks a
snapshot after changes.

### Serialization

`snapshot_to_dict(snapshot)` revalidates the snapshot, including nested values, and
builds new dicts and lists from explicit per-model field lists. Enums become their
values, timestamps ISO 8601 strings, and tuples lists; `None` is kept. Non-finite numbers
and integers beyond 2**53 − 1 are rejected with `ValueError` instead of being changed,
so `json.dumps(data, allow_nan=False)` succeeds for every valid snapshot. Nothing is
written to disk, logged, or sent; there is no deserialization.

Exported fields per model:

| Model | Fields |
| --- | --- |
| `RoutingSnapshot` | `hostname`, `interface`, `ip_address`, `gateway` |
| `NetworkInterface` | `device`, `interface_type`, `state`, `connection` |
| `ListeningSocket` | `protocol`, `state`, `local_address`, `local_port`, `peer_address`, `peer_port` (ports stay strings, `*` allowed) |
| `PingResult` | `target`, `execution_status`, `packets_sent`, `packets_received`, `packet_loss_percent`, `min_rtt_ms`, `avg_rtt_ms`, `max_rtt_ms`, `mdev_ms`, `reachable`, `icmp_errors` |
| `DnsQueryResult` | `hostname`, `execution_status`, `resolved`, `ip_address` |
| `DnsLatencyResult` | `hostname`, `execution_status`, `dns_status`, `resolved`, `dns_latency_ms`, `answer_count` |
| `DnsAddressResult` | `hostname`, `execution_status`, `ipv4_addresses`, `ipv6_addresses` |
| `CollectionIssue` | `code`, `message` (from the fixed message table), `field` |

Never exported: raw routing-table lines (`RoutingSnapshot.routes`, which stays in the
in-memory model), `errors`/`error` texts, `raw_output`, the analysis classifications
`packet_loss_status`, `latency_analysis`, and `rtt_range_analysis`, and suggested service
names. The exported snapshot therefore does not contain all information of the
in-memory `RoutingSnapshot`.

Synthetic example (shortened; routing table unreadable, `nmcli` missing, local
collection with one ping):

```json
{
  "schema_version": 1,
  "snapshot_id": "3f2b8c1e-6d4a-4f0b-9a7e-2c5d1e8f9a01",
  "started_at": "2026-11-26T12:00:00+00:00",
  "finished_at": "2026-11-26T12:00:09+00:00",
  "duration_seconds": 2.25,
  "routing": {
    "operation_id": "routing", "source": "linux_routing", "target": null,
    "status": "partial",
    "value": {"hostname": "example-host", "interface": null, "ip_address": null, "gateway": null},
    "issues": [
      {"code": "measurement_failed", "message": "Diagnostic measurement did not complete.", "field": "routes"},
      {"code": "dependency_unavailable", "message": "A required observation is unavailable.", "field": "interface"}
    ],
    "started_at": "2026-11-26T12:00:01+00:00", "finished_at": "2026-11-26T12:00:02+00:00",
    "duration_seconds": 0.25
  },
  "interfaces": {
    "operation_id": "interfaces", "source": "network_manager", "target": null,
    "status": "unavailable", "value": null,
    "issues": [{"code": "command_unavailable", "message": "Required diagnostic command is unavailable.", "field": null}],
    "started_at": "2026-11-26T12:00:03+00:00", "finished_at": "2026-11-26T12:00:04+00:00",
    "duration_seconds": 0.25
  },
  "pings": [
    {
      "operation_id": "ping.localhost", "source": "icmp_echo", "target": "localhost",
      "status": "completed",
      "value": {
        "target": "localhost", "execution_status": "completed",
        "packets_sent": 4, "packets_received": 4, "packet_loss_percent": 0.0,
        "min_rtt_ms": 0.04, "avg_rtt_ms": 0.05, "max_rtt_ms": 0.07, "mdev_ms": 0.01,
        "reachable": true, "icmp_errors": null
      },
      "issues": [],
      "started_at": "2026-11-26T12:00:07+00:00", "finished_at": "2026-11-26T12:00:08+00:00",
      "duration_seconds": 0.25
    }
  ],
  "dns_query": {
    "operation_id": "dns.query", "source": "system_resolver", "target": "google.com",
    "status": "not_collected", "value": null,
    "issues": [{"code": "not_requested", "message": "Diagnostic operation was not requested.", "field": null}],
    "started_at": null, "finished_at": null, "duration_seconds": null
  },
  "dns_addresses": []
}
```

Privacy and output safety:

- Serialization does not anonymize. IP addresses, hostnames, interface and connection
  names (often Wi-Fi SSIDs), the gateway, and listening sockets are sensitive network
  inventory; share exported snapshots only deliberately.
- JSON escaping does not make text safe for HTML, a GUI, logs, or a terminal. Text with
  C0/C1 control characters is rejected, but other untrusted text is kept as observed,
  including `<script>`, Unicode bidirectional controls, zero-width characters, and the
  line separators U+2028/U+2029; every consumer must escape for its own output context.

## Command execution

Network, DNS, and ping diagnostics use the UI-independent `command_runner`.
`run_command(arguments, allowed_codes=(0,), timeout=10)` accepts a non-empty list
of string arguments, runs without a shell, and returns `CommandResult` with
`arguments`, `stdout`, `stderr`, `return_code`, and `duration_seconds` measured
with `time.monotonic()`. The environment is copied with `LC_ALL=C`, and output is
decoded as UTF-8 with invalid bytes replaced by U+FFFD; the timeout
must be greater than 0 and at most 60 seconds. Invalid arguments (not a non-empty
list of strings, an empty program name, or any NUL character) and invalid timeouts
(non-numeric, boolean, zero, negative, NaN, infinite, or above 60 seconds)
raise `ValidationError` before `subprocess.run()` is called. Later empty arguments
and spaces are allowed.

## Input validation

`validation.py` provides validators that return `None` for an accepted value and
raise `ValidationError(DiagnosticError)` otherwise. They never modify the value.
`ValidationError` is not a `CommandError`: no command was executed.

| Validator | Accepts |
| --- | --- |
| `validate_host(value)` | IPv4 and IPv6 literals and ASCII hostnames (labels of 1-63 letters, digits, and inner hyphens; at most 253 characters, plus one optional trailing dot). Single-label names and punycode are allowed. |
| `validate_ipv4_address(value)` | Four-part dotted-decimal IPv4 addresses without leading zeros. |
| `validate_interface_name(value)` | 1-15 ASCII letters, digits, `_`, `.`, or `-`; not starting with `-` and not `.` or `..`. |
| `validate_command_arguments(arguments)` | A non-empty list of strings with a non-empty program name and no NUL characters. |
| `validate_timeout(timeout)` | An `int` or `float` (not `bool`) greater than 0 and at most `MAX_TIMEOUT_SECONDS` (60). |

`validate_host` rejects empty values, whitespace and control characters, URIs,
CIDR prefixes, ports, brackets, IPv6 scope IDs, Unicode names, values starting
with `-`, `+`, or `@`, and names made only of decimal, octal, or hexadecimal
numeric labels that are not valid dotted-decimal IPv4 addresses (such as
`999.1.2.3`, `0x7f.0.0.1`, or `0x7f`). The resolver would read such names as
alternative IPv4 forms. Names with at least one other label, such as
`0xdeadbeef.example` or `3com`, are still accepted.

Error messages show at most the first 64 characters of a rejected value, with
control characters escaped and `...` marking a truncated value.

Validation happens immediately before each command: `dns_run()`, `ipv_check()`,
and `dns_latency()` validate the host; `dns_latency()` passes it to dig after `-q`.
Ping validates its target, and the gateway read from the routing table must be an
IPv4 address. `get_ip()` runs `ip ... addr show dev` only with an interface name
that the route parser has validated. Command output is validated by `parsers.py`
(see [Output parsing](#output-parsing)).

The interface name rule is conservative: Linux allows some longer-looking or
non-ASCII names that are rejected here. The 60-second maximum is a general upper
bound that keeps timeouts within what `subprocess` can handle; the per-command
timeouts described above (10 seconds) still apply.

Technical failures raise `CommandNotFoundError`, `CommandTimeoutError`, or
`CommandExecutionError`, all derived from `CommandError` and `DiagnosticError`.
They retain command arguments, the reason, the return code when available,
stdout, stderr, and the configured timeout. Timeout output, including partial
byte output, is preserved as text. Original execution exceptions are chained.
The runner does not print, prompt, analyze diagnostics, or import UI modules.

Ping accepts return codes 0 and 1 for diagnostic interpretation. Code 2 and other
unexpected codes are technical failures even when stdout contains output. Getent
accepts codes 0 and 2, so a name not found remains a normal lookup result.

`output.clear_screen()` remains a separate CLI concern and is outside the
diagnostic command runner.

## Diagnostic results

Diagnostics return simple dataclasses from `models.py`. `ExecutionStatus`
separates technical execution (`COMPLETED`, `FAILED`, or `TIMEOUT`) from the
observed network condition. A completed measurement does not establish internet
access, and type hints do not perform runtime validation.

- `PingResult` holds packet counts, packet loss, minimum/average/maximum RTT,
  the separate `mdev_ms` value, ICMP reachability, an optional error, and raw
  stdout. `run_ping_diagnostic()` also fills `packet_loss_status` (a
  `PacketLossStatus` enum), `latency_analysis`, and `rtt_range_analysis` using the
  existing classification rules. Missing analyses remain `None`, with display
  wording chosen by the UI. Missing measurements remain `None`, including after a timeout.
  `reachable=True` means at least one reply was observed; `False` means a
  completed measurement received no replies; `None` means reachability is unknown.
  Complete packet loss is a completed measurement with 100% loss and no RTT data.
  `icmp_errors` holds ping's `+N errors` count (ICMP errors such as destination
  unreachable); it is `None` when ping reported no error count.
- `DnsQueryResult` describes system name resolution, `DnsLatencyResult` describes
  a DNS response and its timing, and `DnsAddressResult` holds separate IPv4/IPv6
  lists. An unsuccessful name lookup or empty address list can be a completed
  operation. NXDOMAIN and SERVFAIL are DNS response codes, not execution failures.
  Missing response time or answer count stays `None`; an indeterminate resolution
  result stays `None` instead of becoming a negative answer.
- `ListeningSocket`, `SocketCounts`, `ServiceInfo`, and `NetworkInterface` replace
  socket/interface dictionaries. Socket endpoint ports remain text so wildcard
  values such as `*` are preserved. Service names remain port-based suggestions;
  a service that uses only UDP or only TCP (for example DHCP or SSH) is not
  suggested for the other protocol.
- `LatencyAnalysis` retains the DNS latency classification's existing measurement
  and status together. Analysis remains in `analysis.py`, outside the data models.
  The service layer stores it in `DnsLatencyResult.latency_analysis` only when a
  latency was measured.
- `ListeningPortReport` and `RoutingSnapshot` combine the results of several engine
  functions for the service layer. A `DnsAddressResult` whose status is not
  `COMPLETED` has unknown, not empty, address lists, and the CLI shows them as
  `Unavailable` instead of `None found`.

Ping helpers return `CommandResult` or raise diagnostic exceptions. `ping_target()`
is the single boundary that converts those exceptions into `PingResult`, preserving
partial stdout and distinguishing timeouts. It does not parse error prefixes.
`run_ping_diagnostic(target)` replaces `handle_ping()` and returns the complete
`PingResult`: target selection, execution, parsing, and available analyses happen
without UI calls. The CLI passes this result to `output.display_ping_report(result)`;
`display_ping_result(result)` only renders raw output and errors and returns `None`.
The report renderer translates the supplied packet-loss status into explanatory
text; it does not classify packet-loss values itself.

Call the public function directly from Python without importing the CLI:

```python
from network_diagnostic.core.ping import run_ping_diagnostic
from network_diagnostic.core.models import ExecutionStatus

result = run_ping_diagnostic("localhost")
if result.execution_status is ExecutionStatus.COMPLETED:
    latency_ms = result.avg_rtt_ms
    latency_classification = result.latency_analysis
    rtt_range_classification = result.rtt_range_analysis
else:
    error_message = result.error
```

The call itself produces no terminal output and asks for no input. It executes
one normal three-packet ping; the same interface can supply a GUI. Supported target
keys remain `localhost`, `gateway`, `internet`, and `google.com`. `ping_target()`
and `parsers.parse_ping_statistics()` provide measurements without the additional analyses.
DNS uses `dns_query()`, `dns_latency()`, and `ipv_check()` for structured results;
network collection/parsing and `analysis` functions can likewise be called directly.

DNS and network command failures continue to raise shared `CommandError`
subclasses for the caller to handle. DNS result models describe returned data;
they do not replace the established exception contract. Unparseable or invalid
dig or getent output yields a failed `DnsLatencyResult` or `DnsQueryResult`,
while a known DNS response code can be completed even when timing or answer-count
information is unavailable.

Routing remains based on the current text output from `ip`; no route model is
introduced. `parsers.select_default_route()` selects the single IPv4 default route
the kernel prefers (lowest metric, missing metric = 0, first route on a tie, routes
marked `dead` skipped). The interface, IPv4 address, and gateway are all read from that
route. A selected default route without `dev`, such as a multipath route with indented
`nexthop` lines or a nexthop group (`nhid`), raises `ParsingError` because it has no
single gateway or interface. Gateway ping
reports separately when there is no IPv4 default route and when the default route
has no gateway (for example, a point-to-point link).

## Output parsing

`parsers.py` turns command output into validated values and result models. Parsers
are pure functions: they run no commands, use no network, print nothing, ask for no
input, and change no external state. Malformed, contradictory, or implausible output
raises `ParsingError(DiagnosticError)`. A missing optional measurement stays `None`
and is never reported as zero.

| Situation | Result |
| --- | --- |
| Input rejected before a command | `ValidationError` |
| Command could not run, failed, or timed out | `CommandError` subclass |
| Malformed or contradictory output | `ParsingError`; converted to a failed `PingResult`, `DnsQueryResult`, or `DnsLatencyResult` by `ping_target()`, `dns_query()`, and `dns_latency()`; raised by network functions |
| Optional value absent from valid output | Field is `None` |
| Valid measurement of a failure, such as 100% loss or NXDOMAIN | `COMPLETED` result |

A failed parse carries no partial measurement values; ping keeps the raw output.
`main.py` reports every `DiagnosticError` as an expected error and returns to the menu.

| Parser | Validation |
| --- | --- |
| `parse_ping_statistics(output, target)` | Packet counts are ASCII integers, at least one packet was sent, and received ≤ sent. Loss is 0-100% and within one percentage point of the counts (this covers iputils `%g`, macOS `%.1f`, and BusyBox integer rounding). RTT values are non-negative decimals (no NaN, infinity, or exponent) with min ≤ avg ≤ max. Duplicate replies (`+N duplicates`) are not counted as received. An `+N errors` count must be an integer and is returned as `icmp_errors`. Complete loss without an RTT line is a completed measurement with `None` RTT values; an RTT line without received packets, received packets without an RTT line, or repeated statistics lines are errors. Four RTT values are required. |
| `parse_getent_hosts(output)` | Every address must be a valid IPv4 or IPv6 address; returns the first one or `None`. |
| `parse_getent_addresses(output, version)` | Returns unique addresses of one version; invalid and other-version lines are skipped. IPv4-mapped IPv6 addresses (`::ffff:a.b.c.d`, returned by `getent ahostsv6` for IPv4-only names) are not IPv6 addresses and are skipped. |
| `parse_dig_output(output, hostname)` | Requires a response code (`[A-Z][A-Z0-9]*`); the answer count is an integer and the query time a non-negative decimal in `msec`. A present but invalid value or conflicting repeated values are errors; an absent value is `None`. The response code and the query time stay separate. |
| `parse_ss_output(lines)` | Ports are `*` or 0-65535; addresses are `*`, IPv4, or IPv6, optionally with a `%interface` scope inside or after the brackets. Empty lines and a `Netid` header are skipped; any other line that does not match raises `ParsingError`, so a partial socket list is never shown as complete. |
| `parse_nmcli_output(lines)`, `parse_nmcli_states(output)` | Empty lines are skipped. A line with a wrong field count or an empty device or state raises `ParsingError("Unrecognized line in nmcli output")`. Empty type and connection fields are allowed. |
| `select_default_route(route_lines)` | Returns the fields of the default route with the lowest metric (missing = 0; first on a tie), or `None`. Incomplete lines that end with `via`, `dev`, `src`, or `metric` and routes marked `dead` are skipped. A metric must be an integer 0-4294967295. A selected route without `dev` (multipath or nexthop group) raises `ParsingError`. |
| `parse_default_gateway()`, `parse_default_source()`, `parse_default_interface()` | Read from the route chosen by `select_default_route()`. The `via` and `src` values must be IPv4 addresses and `dev` a valid interface name (phase 8 validators). |
| `parse_interface_ipv4_address(output)` | The first `inet` address must be a valid IPv4 address. |
| `parse_port(text)` | A decimal string in the range 0-65535. |

Analysis rejects implausible input: service suggestions only use ports 1-65535
(int or decimal string), and latency and RTT-range classifiers raise `ValueError`
for negative, non-finite, or reversed values.

DNS server information is not part of the current functionality and is not parsed.

## Measurement limits

- Ping tests ICMP reachability to a specific target. A failed ping does not prove that the entire internet connection is down; ICMP may be blocked.
- Each ping test sends three packets. This small sample does not establish long-term connection stability.
- The RTT range is the maximum minus the minimum measured round-trip time. It is not a dedicated jitter measurement.
- Latency classifications are simple project-defined thresholds, not universal quality standards.
- getent uses the system name-resolution configuration, including sources such as /etc/hosts. Its results are not necessarily obtained through a DNS network query.
- IPv4 and IPv6 lookups show addresses returned by the system resolver. They do not test connectivity over either address family, and results can depend on host configuration.
- DNS latency measures one query to the server in `/etc/resolv.conf`, often a local caching resolver such as systemd-resolved (127.0.0.53). A cached answer measures the local resolver, not the upstream DNS server. The CLI states this with the result. The default dig lookup requests an A record.
- Socket counts count rows, not unique port numbers. One port may appear on several addresses.
- Service names are port-based suggestions. They do not identify the actual application or prove external accessibility.
- The routing overview uses the IPv4 default route with the lowest metric in the main routing table. Policy routing rules (`ip rule`) are not evaluated. Routes marked `dead` are skipped. `linkdown` routes are kept, as the kernel uses them by default (`ignore_routes_with_linkdown=0`). Multipath default routes are reported as unsupported instead of choosing one next hop.
- A listening address is shown as reported by `ss`; the tool does not classify whether it is reachable only locally (loopback) or from other hosts.
- NetworkManager's `connected` and `connected (externally)` states do not prove internet access.

## Development

Create and activate a virtual environment, install the test dependencies, and install
the package in editable mode so that source changes take effect without reinstalling:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

The test dependencies (pytest, pytest-cov) are listed only in `requirements-dev.txt`.
They are not package metadata, and the Dockerfile installs them from that file.
pytest and coverage settings are in `pyproject.toml`.

## Testing

Tests import the installed package (`network_diagnostic`). The `src` layout keeps the
project root free of importable modules, so the tests cannot accidentally use files from
the working directory; install the package first (see [Development](#development)).

Run all tests from the project root:

```bash
python -m pytest -v
```

Measure line and branch coverage for the whole package (the measured package and branch
coverage are configured in `pyproject.toml`):

```bash
python -m pytest --cov --cov-report=term-missing
```

## Building

Build a wheel into `dist/` without installing anything:

```bash
python -m pip wheel . --no-deps -w dist
```

Install the built wheel into another virtual environment with
`python -m pip install dist/network_diagnostic-0.1.0-py3-none-any.whl`. The wheel contains
only the `network_diagnostic` package and the `network-diagnostic` command; it does not
contain the tests or the Linux system tools.

## Continuous integration

GitHub Actions runs the workflow `.github/workflows/ci.yml` on every push, on pull requests
to `main`, on manual start (`workflow_dispatch`), and weekly on Monday at 04:17 UTC.
All jobs run on `ubuntu-24.04` with read-only repository access and no secrets.

| Job | What it checks |
| --- | --- |
| Tests (Python 3.11–3.14) | Full test suite on each version (`fail-fast: false`), editable install from `requirements-dev.txt` |
| Coverage (Python 3.12) | Line coverage ≥ 99 % and branch coverage ≥ 98 %, checked separately from the JSON report by `.github/scripts/check_coverage.py`; full suite again in reverse order |
| Package build and install | Builds the wheel, checks its contents and metadata against `pyproject.toml` (`.github/scripts/check_package.py`), installs it into a clean virtual environment, smoke-tests both entry points, and runs the tests against the installed wheel |
| Dependency audit | `pip-audit` (pinned in the workflow, CI only) checks the exact packages installed by the test jobs, including transitive dependencies and pip itself; fails on any known vulnerability or audit error |
| CI Quality Gate | Succeeds only when all jobs above succeeded; use it as the required status check |

Reports are kept as workflow artifacts for 14 days: JUnit test reports, installed
dependency lists, coverage reports (JSON, XML, HTML), the wheel, and the JSON audit reports.
Actions are pinned to full commit SHAs. Dependabot (`.github/dependabot.yml`) opens weekly
update pull requests for `requirements-dev.txt` and the actions; they are merged manually.
The pinned pip and pip-audit versions (`PIP_VERSION`, `PIP_AUDIT_VERSION` in the workflow)
are not covered by Dependabot and are updated manually.

Run the coverage and reverse-order checks locally:

```bash
python -m pytest --cov --cov-report=json:reports/coverage.json
python .github/scripts/check_coverage.py reports/coverage.json --min-line 99 --min-branch 98
PYTHONPATH=.github/scripts python -m pytest -p reverse_order
```

## Test design

Tests are in `tests/`, with reusable command output in `tests/fixtures/`.
System commands are mocked, so tests need no internet connection, real network
configuration, diagnostic command installations, or root privileges. Shared test
fixtures fail immediately if a test attempts an unmocked system command. Menu
tests use finite input sequences, and date-dependent output uses a fixed clock.
Architecture tests import a fresh engine with CLI modules unavailable and verify
silent ping success, complete packet loss, technical failure, and timeout handling.
They also check that `parsers.py` imports only models, exceptions, validation, and
standard-library modules and runs without commands, input, or output, that the
package has no circular imports, and that importing any package module runs no
commands and produces no output. Architecture tests read the module sources from the
installed package. Parser tests in
`tests/test_parsers.py` call the parsers directly with realistic output, without
subprocess mocks.
Report tests check that rendering neither mutates data nor executes commands.
Service layer tests in `tests/test_diagnostic_service.py` mock engine functions in the
`diagnostic_service` namespace and also run a few end-to-end cases through mocked
subprocess calls. Architecture tests check that the service layer imports only engine
modules, never prints, prompts, or starts processes, and that neither the engine
imports the service layer nor the CLI imports engine modules directly.

## Validation

The automated tests cover parsing, analysis, input validation, command errors and
timeouts, CLI navigation, and report contents. Remaining limitations are listed in
[known issues](tests/KNOWN_ISSUES.md).

Mocked commands do not establish compatibility with every Linux installation or
verify real connectivity, DNS, or terminal rendering. Before relying on the tool,
exercise each menu action on the target machine with the network connected and disconnected.
CI also runs the tests in reverse order to detect order dependencies (see
[Continuous integration](#continuous-integration)).

## Known limitations

- The minimum version is Python 3.11. Locally the test suite has been run only on
  Python 3.12; CI runs it on 3.11–3.14, but compatibility is verified only once those CI
  runs have passed. CI runs on Ubuntu 24.04 with mocked system commands and does not test
  real network behaviour or other distributions.
- The Linux system tools listed in [Requirements](#requirements) are not installed by
  pip and must be installed separately.
- The Dockerfile does not install the package. Running the tests in a container requires
  installing the package (for metadata) and, to test the current source, `PYTHONPATH=/app/src`.
- The package is not published to PyPI, and building it requires setuptools, which pip
  downloads from PyPI when it is not already available.
- A diagnostic snapshot is collected sequentially and is not atomic; it cannot be
  cancelled as a whole and has no overall time limit. With the current per-command
  timeouts the default request can take about 200 seconds in the worst case; this is an
  estimate, not a guaranteed upper bound.
- Open diagnostic limitations are listed in [known issues](tests/KNOWN_ISSUES.md).
