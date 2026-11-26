from command_runner import run_command
from parsers import (
    parse_default_gateway, parse_default_interface, parse_default_source,
    parse_interface_ipv4_address, parse_nmcli_states,
)


def nmcli_status():
    result = run_command(
        [
            "nmcli",
            "-t",
            "-e",
            "yes",
            "-f",
            "DEVICE,TYPE,STATE,CONNECTION",
            "device",
            "status"
        ]
    )

    return result.stdout.strip().splitlines()


def get_listening_sockets():
    result = run_command(["ss", "-H", "-tuln"])

    return result.stdout.strip().splitlines()


# NetworkManager device states that mean the device has an active connection.
# "connected (externally)" is a connection that NetworkManager did not create.
CONNECTED_STATES = ("connected", "connected (externally)")


def get_network_plugin():
    result = run_command(
        [
            "nmcli",
            "-t",
            "-e",
            "yes",
            "-f",
            "DEVICE,STATE",
            "device",
            "status"
        ]
    )

    for interface, state in parse_nmcli_states(result.stdout):
        if interface != "lo" and state in CONNECTED_STATES:
            return True

    return False


def get_ip_route():
    result = run_command(["ip", "-4", "route"])

    return result.stdout.strip().splitlines()


def get_hostname():
    result = run_command(["hostname"])

    return result.stdout.strip()


def get_ip(route_data):
    source = parse_default_source(route_data)

    if source is not None:
        return source

    interface = parse_default_interface(route_data)

    if interface is None:
        return None

    # parse_default_interface() has validated the name before the command runs.
    result = run_command(
        ["ip", "-4", "-o", "addr", "show", "dev", interface]
    )

    return parse_interface_ipv4_address(result.stdout)


def get_gateway(route_data=None):
    if route_data is None:
        route_data = get_ip_route()

    return parse_default_gateway(route_data)


def get_nic(route_data):
    return parse_default_interface(route_data)
