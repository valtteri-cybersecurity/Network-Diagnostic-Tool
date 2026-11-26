import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from network_diagnostic.core import network
from network_diagnostic.core.exceptions import CommandError, ParsingError
from network_diagnostic.core.models import CommandResult


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "network"


@pytest.mark.parametrize("function, arguments, fixture_name", [
    (network.nmcli_status,
     ["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION",
      "device", "status"], "nmcli_devices.txt"),
    (network.get_listening_sockets, ["ss", "-H", "-tuln"], "ss_sockets.txt"),
    (network.get_ip_route, ["ip", "-4", "route"], "ip_route.txt"),
])
def test_command_collectors_return_output_lines(function, arguments, fixture_name, monkeypatch):
    output = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    command = Mock(return_value=CommandResult(arguments, "\n" + output + "\n", "", 0, 0.0))
    monkeypatch.setattr(network, "run_command", command)

    result = function()

    assert result == output.splitlines()
    command.assert_called_once_with(arguments)


@pytest.mark.parametrize("function", [
    network.nmcli_status, network.get_listening_sockets, network.get_ip_route,
])
@pytest.mark.parametrize("output", ["", " \n\t"])
def test_command_collectors_with_empty_output(function, output, monkeypatch):
    monkeypatch.setattr(network, "run_command", Mock(
        return_value=CommandResult([], output, "", 0, 0.0),
    ))

    result = function()

    assert result == []


@pytest.mark.parametrize("function", [
    network.nmcli_status, network.get_listening_sockets, network.get_ip_route,
    network.get_hostname, network.get_network_plugin, network.get_gateway,
])
def test_command_collectors_propagate_command_errors(function, monkeypatch):
    error = CommandError("command unavailable")
    monkeypatch.setattr(network, "run_command", Mock(side_effect=error))

    with pytest.raises(CommandError) as caught:
        function()

    assert caught.value is error


def test_get_network_plugin_detects_connected_non_loopback_interface(monkeypatch):
    output = (FIXTURE_DIRECTORY / "nmcli_states.txt").read_text(encoding="utf-8")
    command = Mock(return_value=CommandResult([], output, "", 0, 0.0))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_network_plugin()

    assert result is True
    command.assert_called_once_with(
        ["nmcli", "-t", "-e", "yes", "-f", "DEVICE,STATE", "device", "status"],
    )


@pytest.mark.parametrize("output, expected", [
    ("", False),
    (" \n", False),
    ("lo:connected\n", False),
    ("wlp2s0:disconnected\nenp3s0:unavailable\n", False),
    ("enp3s0:connecting (getting IP configuration)\n", False),
    ("lo:connected (externally)\n", False),
    (r"veth\:1:connected" + "\n", True),
])
def test_get_network_plugin_handles_interface_states(output, expected, monkeypatch):
    monkeypatch.setattr(network, "run_command", Mock(
        return_value=CommandResult([], output, "", 0, 0.0),
    ))

    result = network.get_network_plugin()

    assert result is expected


@pytest.mark.parametrize("output, expected", [
    ("workstation\n", "workstation"),
    ("  workstation.example.test  \n", "workstation.example.test"),
    ("", ""),
    (" \n\t", ""),
])
def test_get_hostname_strips_command_output(output, expected, monkeypatch):
    command = Mock(return_value=CommandResult(["hostname"], output, "", 0, 0.0))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_hostname()

    assert result == expected
    command.assert_called_once_with(["hostname"])


def test_get_ip_uses_default_route_source_without_running_command(monkeypatch):
    routes = ["", "192.0.2.0/24 dev enp3s0 src 192.0.2.99",
              "default via 192.0.2.1 dev enp3s0 src 192.0.2.10"]
    command = Mock(side_effect=AssertionError("No address command expected"))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_ip(routes)

    assert result == "192.0.2.10"
    command.assert_not_called()


def test_get_ip_falls_back_to_interface_and_uses_first_ipv4_address(monkeypatch):
    routes = (FIXTURE_DIRECTORY / "ip_route.txt").read_text(encoding="utf-8").splitlines()
    output = (FIXTURE_DIRECTORY / "ip_address.txt").read_text(encoding="utf-8")
    command = Mock(return_value=CommandResult([], output, "", 0, 0.0))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_ip(routes)

    assert result == "192.0.2.10"
    command.assert_called_once_with(["ip", "-4", "-o", "addr", "show", "dev", "enp3s0"])


@pytest.mark.parametrize("output", [
    "", " \n", "invalid output\n", "2: enp3s0 inet\n",
    "2: enp3s0 inet6 2001:db8::10/64 scope global\n",
])
def test_get_ip_returns_none_when_no_ipv4_address_is_available(output, monkeypatch):
    monkeypatch.setattr(network, "run_command", Mock(
        return_value=CommandResult([], output, "", 0, 0.0),
    ))

    result = network.get_ip(["default via 192.0.2.1 dev enp3s0 src"])

    assert result is None


def test_get_ip_skips_malformed_address_line_before_valid_address(monkeypatch):
    output = "invalid\n2: enp3s0 inet\n2: enp3s0 inet 192.0.2.10/24 scope global\n"
    monkeypatch.setattr(network, "run_command", Mock(
        return_value=CommandResult([], output, "", 0, 0.0),
    ))

    result = network.get_ip(["default dev enp3s0"])

    assert result == "192.0.2.10"


@pytest.mark.parametrize("routes", [
    [], [""], ["invalid output"],
    ["192.0.2.0/24 dev enp3s0 src 192.0.2.10"],
    ["default dev"],
])
def test_get_ip_without_default_interface_returns_none(routes, monkeypatch):
    command = Mock(side_effect=AssertionError("No address command expected"))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_ip(routes)

    assert result is None
    command.assert_not_called()


def test_get_ip_propagates_address_command_error(monkeypatch):
    error = CommandError("ip failed")
    monkeypatch.setattr(network, "run_command", Mock(side_effect=error))

    with pytest.raises(CommandError) as caught:
        network.get_ip(["default dev enp3s0"])

    assert caught.value is error


def test_get_gateway_reads_routes_when_not_provided(monkeypatch):
    routes = (FIXTURE_DIRECTORY / "ip_route.txt").read_text(encoding="utf-8").splitlines()
    route_command = Mock(return_value=routes)
    monkeypatch.setattr(network, "get_ip_route", route_command)

    result = network.get_gateway()

    assert result == "192.0.2.1"
    route_command.assert_called_once_with()


@pytest.mark.parametrize("routes, expected", [
    (["", "invalid", "192.0.2.0/24 dev enp3s0", "default via 192.0.2.1 dev enp3s0"],
     "192.0.2.1"),
    (["default via 192.0.2.1 dev enp3s0", "default via 198.51.100.1 dev wlan0"],
     "192.0.2.1"),
    (["default via", "default via 192.0.2.1 dev enp3s0"], "192.0.2.1"),
    ([], None),
    ([""], None),
    (["invalid"], None),
    (["192.0.2.0/24 dev enp3s0 src 192.0.2.10"], None),
    (["default dev ppp0 scope link"], None),
    (["default via"], None),
])
def test_get_gateway_uses_supplied_routes_without_querying(routes, expected, monkeypatch):
    route_command = Mock(side_effect=AssertionError("No route command expected"))
    monkeypatch.setattr(network, "get_ip_route", route_command)

    result = network.get_gateway(routes)

    assert result == expected
    route_command.assert_not_called()


@pytest.mark.parametrize("routes, expected", [
    (["", "invalid", "192.0.2.0/24 dev wrong", "default via 192.0.2.1 dev enp3s0"],
     "enp3s0"),
    (["default via 192.0.2.1 dev enp3s0", "default dev wlan0"], "enp3s0"),
    (["default dev", "default dev wlan0"], "wlan0"),
    (["default dev ppp0 scope link"], "ppp0"),
    ([], None),
    ([""], None),
    (["invalid"], None),
    (["192.0.2.0/24 dev enp3s0"], None),
    (["default dev"], None),
])
def test_get_nic_reads_first_available_default_route_interface(routes, expected):
    result = network.get_nic(routes)

    assert result == expected


@pytest.mark.parametrize("function, arguments, fixture_name", [
    (network.nmcli_status,
     ["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
     "nmcli_devices.txt"),
    (network.get_listening_sockets, ["ss", "-H", "-tuln"], "ss_sockets.txt"),
    (network.get_ip_route, ["ip", "-4", "route"], "ip_route.txt"),
])
def test_network_collectors_integrate_shared_runner(function, arguments, fixture_name, subprocess_run):
    raw = (FIXTURE_DIRECTORY / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(arguments, 0, raw, "")

    result = function()

    assert result == raw.strip().splitlines()
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args == (arguments,)
    assert subprocess_run.call_args.kwargs["timeout"] == 10
    assert subprocess_run.call_args.kwargs["shell"] is False
    assert subprocess_run.call_args.kwargs["env"]["LC_ALL"] == "C"


def test_get_ip_integrates_address_query_with_shared_runner(subprocess_run):
    routes = (FIXTURE_DIRECTORY / "ip_route.txt").read_text(encoding="utf-8").splitlines()
    raw = (FIXTURE_DIRECTORY / "ip_address.txt").read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, raw, "")

    result = network.get_ip(routes)

    assert result == "192.0.2.10"
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["ip", "-4", "-o", "addr", "show", "dev", "enp3s0"]


@pytest.mark.parametrize("interface", ["-f", "--help", "a" * 16, "eth0;id", "+eth0", "..", "ethö"])
def test_get_ip_rejects_invalid_route_interface_before_address_command(interface, subprocess_run):
    with pytest.raises(ParsingError, match="^Invalid default route interface: Invalid interface name: "):
        network.get_ip([f"default via 192.0.2.1 dev {interface}"])

    subprocess_run.assert_not_called()


def test_get_ip_validates_interface_only_when_address_command_is_needed(subprocess_run):
    result = network.get_ip(["default via 192.0.2.1 dev -f src 192.0.2.10"])

    assert result == "192.0.2.10"
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("function, routes, error", [
    (network.get_gateway, ["default via not-an-address dev enp3s0"],
     "Invalid default gateway: Invalid IPv4 address: 'not-an-address'"),
    (network.get_gateway, ["default via 999.1.2.3 dev enp3s0"],
     "Invalid default gateway: Invalid IPv4 address: '999.1.2.3'"),
    (network.get_nic, ["default via 192.0.2.1 dev -f"],
     "Invalid default route interface: Invalid interface name: '-f'"),
    (network.get_ip, ["default dev enp3s0 src not-an-address"],
     "Invalid default route source address: Invalid IPv4 address: 'not-an-address'"),
])
def test_route_values_are_validated_before_use(function, routes, error, subprocess_run):
    with pytest.raises(ParsingError) as caught:
        function(routes)

    assert str(caught.value) == error
    subprocess_run.assert_not_called()


@pytest.mark.parametrize("output, address", [
    ("2: enp3s0    inet 999.1.2.3/24 scope global enp3s0\n", "999.1.2.3"),
    ("2: enp3s0    inet not-an-address/24 scope global enp3s0\n", "not-an-address"),
])
def test_get_ip_rejects_invalid_interface_address_output(output, address, subprocess_run):
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, output, "")

    with pytest.raises(ParsingError) as caught:
        network.get_ip(["default via 192.0.2.1 dev enp3s0"])

    assert str(caught.value) == f"Invalid interface address: Invalid IPv4 address: {address!r}"


# --- Phase 10 regressions -----------------------------------------------------


def test_get_ip_reads_address_from_selected_default_route(monkeypatch):
    routes = ["default via 192.0.2.1 dev enp3s0 metric 100",
              "default via 198.51.100.1 dev wlan0 src 198.51.100.10 metric 600"]
    command = Mock(return_value=CommandResult(
        [], "2: enp3s0    inet 192.0.2.10/24 brd 192.0.2.255 scope global enp3s0\n", "", 0, 0.0,
    ))
    monkeypatch.setattr(network, "run_command", command)

    result = network.get_ip(routes)

    assert result == "192.0.2.10"
    assert network.get_nic(routes) == "enp3s0"
    assert network.get_gateway(routes) == "192.0.2.1"
    command.assert_called_once_with(["ip", "-4", "-o", "addr", "show", "dev", "enp3s0"])


@pytest.mark.parametrize("output, expected", [
    ("enp3s0:connected (externally)\n", True),
    ("lo:connected (externally)\nenp3s0:disconnected\n", False),
    ("enp3s0:connecting (getting IP configuration)\n", False),
])
def test_get_network_plugin_accepts_externally_connected_interface(output, expected, monkeypatch):
    monkeypatch.setattr(network, "run_command", Mock(return_value=CommandResult([], output, "", 0, 0.0)))

    result = network.get_network_plugin()

    assert result is expected


@pytest.mark.parametrize("output", ["invalid\nwlp2s0:connected\n", "wlp2s0:connected:extra\n"])
def test_get_network_plugin_reports_unrecognized_output_instead_of_guessing(output, monkeypatch):
    # Phase 13: previously the first case returned True and the second False.
    monkeypatch.setattr(network, "run_command", Mock(return_value=CommandResult([], output, "", 0, 0.0)))

    with pytest.raises(ParsingError, match="^Unrecognized line in nmcli output$"):
        network.get_network_plugin()
