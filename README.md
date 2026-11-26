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

Inputs that are rejected before a command runs raise `ValidationError`. The CLI reports them like other expected errors and returns to the main menu; ping reports them as a failed `PingResult`.

A hostname not found by getent is reported as an unsuccessful lookup. DNS response codes such as NXDOMAIN are displayed separately from response time: a fast response does not necessarily mean that the requested name exists.

## Project structure

| File | Responsibility |
| --- | --- |
| main.py | Menu navigation and coordination |
| command_runner.py | Safe command execution, timeouts, and execution timing |
| exceptions.py | Shared diagnostic, validation, parsing, and command exceptions |
| validation.py | Input validation before commands are executed |
| parsers.py | Pure, validating parsers for ping, DNS, and network command output |
| models.py | Typed command, diagnostic, socket, interface, and analysis data models |
| network.py | Network data collection |
| ping.py | UI-independent ping execution and analysis coordination |
| dns.py | Hostname resolution, address lookups, and DNS measurements |
| analysis.py | Socket filtering, service-name suggestions, and measurement classifications |
| output.py | Menus, reports, error messages, and screen output |

The diagnostic engine consists of `ping`, `dns`, `network`, `analysis`, `parsers`,
`models`, `exceptions`, `validation`, and `command_runner`. It executes commands, parses their output,
builds result models, and classifies measurements. It never prints, prompts,
clears the screen, or imports `main` or `output`. Importing modules runs no commands.

`main.py` owns input, navigation, diagnostic selection, and expected-error handling.
It forwards engine results to `output.py`, which only renders supplied data and
analyses. Output functions return no analysis data and do not modify result models
or run diagnostics. All print calls and terminal clearing remain in `output.py`.

Dependencies flow from CLI to engine: `main` calls diagnostic/analysis functions
and `output`; `output` uses only the shared models and standard-library formatting.
`ping` uses `network`, `analysis`, `parsers`, and the command runner; `dns` and `network`
use `parsers` and the runner; `analysis` uses models and `parsers.parse_port()`.
`main` uses `parsers` for socket and NetworkManager rows. `ping`, `dns`, `network`,
`parsers`, and the runner use `validation`, which depends only on shared exceptions.
`parsers` uses models, shared exceptions, and `validation` only. The runner uses
models and shared exceptions.
There are no engine-to-CLI dependencies or circular imports. A future GUI can
call the same engine functions and render the same results without CLI imports.

## Command execution

Network, DNS, and ping diagnostics use the UI-independent `command_runner`.
`run_command(arguments, allowed_codes=(0,), timeout=10)` accepts a non-empty list
of string arguments, runs without a shell, and returns `CommandResult` with
`arguments`, `stdout`, `stderr`, `return_code`, and `duration_seconds` measured
with `time.monotonic()`. The environment is copied with `LC_ALL=C`; the timeout
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
- `DnsQueryResult` describes system name resolution, `DnsLatencyResult` describes
  a DNS response and its timing, and `DnsAddressResult` holds separate IPv4/IPv6
  lists. An unsuccessful name lookup or empty address list can be a completed
  operation. NXDOMAIN and SERVFAIL are DNS response codes, not execution failures.
  Missing response time or answer count stays `None`; an indeterminate resolution
  result stays `None` instead of becoming a negative answer.
- `ListeningSocket`, `SocketCounts`, `ServiceInfo`, and `NetworkInterface` replace
  socket/interface dictionaries. Socket endpoint ports remain text so wildcard
  values such as `*` are preserved. Service names remain port-based suggestions.
- `LatencyAnalysis` retains the DNS latency classification's existing measurement
  and status together. Analysis remains in `analysis.py`, outside the data models.

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
from ping import run_ping_diagnostic
from models import ExecutionStatus

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
introduced, and the default route selection is unchanged.

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
| `parse_ping_statistics(output, target)` | Packet counts are ASCII integers, at least one packet was sent, and received ≤ sent. Loss is 0-100% and within one percentage point of the counts (this covers iputils `%g`, macOS `%.1f`, and BusyBox integer rounding). RTT values are non-negative decimals (no NaN, infinity, or exponent) with min ≤ avg ≤ max. Duplicate replies (`+N duplicates`) are not counted as received. Complete loss without an RTT line is a completed measurement with `None` RTT values; an RTT line without received packets, received packets without an RTT line, or repeated statistics lines are errors. Four RTT values are required. |
| `parse_getent_hosts(output)` | Every address must be a valid IPv4 or IPv6 address; returns the first one or `None`. |
| `parse_getent_addresses(output, version)` | Returns unique addresses of one version; invalid and other-version lines are skipped. |
| `parse_dig_output(output, hostname)` | Requires a response code (`[A-Z][A-Z0-9]*`); the answer count is an integer and the query time a non-negative decimal in `msec`. A present but invalid value or conflicting repeated values are errors; an absent value is `None`. The response code and the query time stay separate. |
| `parse_ss_output(lines)` | Ports are `*` or 0-65535; addresses are `*`, IPv4, or IPv6, optionally with a `%interface` scope inside or after the brackets. Lines that do not match are skipped. |
| `parse_nmcli_output(lines)`, `parse_nmcli_states(output)` | Lines with a wrong field count or an empty device or state are skipped. |
| `parse_default_gateway()`, `parse_default_source()`, `parse_default_interface()` | The `via` and `src` values must be IPv4 addresses and `dev` a valid interface name (phase 8 validators). |
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
- DNS latency measures one query to the configured resolver. Caching affects the result. The default dig lookup requests an A record.
- Socket counts count rows, not unique port numbers. One port may appear on several addresses.
- Service names are port-based suggestions. They do not identify the actual application or prove external accessibility.
- The routing overview uses the first default IPv4 route. VPNs, multiple default routes, and policy routing may require more detailed investigation.
- NetworkManager's connected state does not prove internet access.

## Testing

Create and activate a development environment, then install the test dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Run all tests:

```bash
python -m pytest -v
```

Measure line and branch coverage for the whole application:

```bash
python -m pytest --cov=analysis --cov=network --cov=ping --cov=dns --cov=main --cov=output --cov=command_runner --cov=exceptions --cov=models --cov=validation --cov=parsers --cov-branch --cov-report=term-missing
```

Tests are in `tests/`, with reusable command output in `tests/fixtures/`.
System commands are mocked, so tests need no internet connection, real network
configuration, diagnostic command installations, or root privileges. Shared test
fixtures fail immediately if a test attempts an unmocked system command. Menu
tests use finite input sequences, and date-dependent output uses a fixed clock.
Architecture tests import a fresh engine with CLI modules unavailable and verify
silent ping success, complete packet loss, technical failure, and timeout handling.
They also check that `parsers.py` imports only models, exceptions, validation, and
standard-library modules and runs without commands, input, or output. Parser tests in
`tests/test_parsers.py` call the parsers directly with realistic output, without
subprocess mocks.
Report tests check that rendering neither mutates data nor executes commands.

## Validation

The automated tests cover parsing, analysis, input validation, command errors and
timeouts, CLI navigation, and report contents. Some tests explicitly preserve known incorrect
behavior to protect the current implementation during refactoring; see
[known issues](tests/KNOWN_ISSUES.md). Passing tests do not mean those issues are fixed.

Mocked commands do not establish compatibility with every Linux installation or
verify real connectivity, DNS, or terminal rendering. Before relying on the tool,
exercise each menu action on the target machine with the network connected and disconnected.
