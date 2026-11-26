from command_runner import run_command
from models import ListeningSocket, NetworkInterface


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


def split_nmcli_fields(line):
    fields = []
    characters = []
    escaped = False

    for character in line:
        if escaped:
            characters.append(character)
            escaped = False

        elif character == "\\":
            escaped = True

        elif character == ":":
            fields.append("".join(characters))
            characters = []

        else:
            characters.append(character)

    if escaped:
        characters.append("\\")

    fields.append("".join(characters))

    return fields


def parse_nmcli_output(results: list[str]) -> list[NetworkInterface]:
    parsed = []

    for line in results:
        parts = split_nmcli_fields(line)

        if len(parts) != 4:
            continue

        device, interface_type, state, connection = parts

        parsed.append(NetworkInterface(
            device=device, interface_type=interface_type,
            state=state, connection=connection,
        ))

    return parsed


def get_listening_sockets():
    result = run_command(["ss", "-H", "-tuln"])

    return result.stdout.strip().splitlines()


def parse_ss_output(results: list[str]) -> list[ListeningSocket]:
    sockets = []

    for line in results:
        parts = line.split()

        if len(parts) < 6 or parts[0] == "Netid":
            continue

        protocol = parts[0]
        state = parts[1]
        local_address_port = parts[4]
        peer_address_port = parts[5]

        try:
            local_address, local_port = local_address_port.rsplit(":", 1)
            peer_address, peer_port = peer_address_port.rsplit(":", 1)
        except ValueError:
            continue

        sockets.append(ListeningSocket(
            protocol=protocol, state=state,
            local_address=local_address, local_port=local_port,
            peer_address=peer_address, peer_port=peer_port,
        ))

    return sockets


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

    for line in result.stdout.splitlines():
        parts = split_nmcli_fields(line)

        if len(parts) != 2:
            continue

        interface, state = parts

        if interface != "lo" and state == "connected":
            return True

    return False


def get_ip_route():
    result = run_command(["ip", "-4", "route"])

    return result.stdout.strip().splitlines()


def get_hostname():
    result = run_command(["hostname"])

    return result.stdout.strip()


def get_ip(route_data):
    for line in route_data:
        parts = line.split()

        if not parts or parts[0] != "default":
            continue

        if "src" in parts:
            index = parts.index("src")

            if index + 1 < len(parts):
                return parts[index + 1]

    interface = get_nic(route_data)

    if interface is None:
        return None

    result = run_command(
        ["ip", "-4", "-o", "addr", "show", "dev", interface]
    )

    for line in result.stdout.splitlines():
        parts = line.split()

        if "inet" in parts:
            index = parts.index("inet")

            if index + 1 < len(parts):
                return parts[index + 1].split("/")[0]

    return None


def get_gateway(route_data=None):
    if route_data is None:
        route_data = get_ip_route()

    for line in route_data:
        parts = line.split()

        if not parts or parts[0] != "default":
            continue

        if "via" in parts:
            index = parts.index("via")

            if index + 1 < len(parts):
                return parts[index + 1]

    return None


def get_nic(route_data):
    for line in route_data:
        parts = line.split()

        if not parts or parts[0] != "default":
            continue

        if "dev" in parts:
            index = parts.index("dev")

            if index + 1 < len(parts):
                return parts[index + 1]

    return None
