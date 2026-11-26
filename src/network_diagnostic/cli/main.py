from network_diagnostic import diagnostic_service
from network_diagnostic.cli import output

from network_diagnostic.core.exceptions import DiagnosticError


def read_menu_choice(max_choice):
    while True:
        output.display_menu_prompt(max_choice)

        try:
            choice = int(input())
        except ValueError:
            output.display_menu_error(max_choice)
            continue

        if 1 <= choice <= max_choice:
            return choice

        output.display_menu_error(max_choice)


def display_errors(messages):
    """Display each distinct error message once, in order."""
    shown = []
    for message in messages:
        if message not in shown:
            output.display_command_error(message)
            shown.append(message)


def main():
    output.clear_screen()

    while True:
        try:
            try:
                network_connected = diagnostic_service.get_connection_status()
            except DiagnosticError as error:
                output.display_command_error(str(error))
                network_connected = None

            output.mainmenu1(network_connected)

            choice = read_menu_choice(5)

            if choice == 5:
                output.clear_screen()
                break

            elif choice == 1:
                while True:
                    output.mainmenu2()
                    submenu_choice = read_menu_choice(5)

                    if submenu_choice == 5:
                        output.clear_screen()
                        break

                    targets = {
                        1: "localhost",
                        2: "gateway",
                        3: "internet",
                        4: "google.com"
                    }

                    output.clear_screen()
                    report = diagnostic_service.run_ping_diagnostic(targets[submenu_choice])
                    output.display_ping_report(report)

            elif choice == 2:
                while True:
                    output.mainmenu3()
                    submenu_choice = read_menu_choice(4)

                    if submenu_choice == 4:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    if submenu_choice == 1:
                        result = diagnostic_service.run_dns_query()
                        output.dns_query_output(result)

                    elif submenu_choice == 2:
                        result = diagnostic_service.run_dns_latency_test()
                        output.dns_latency_output(result, result.latency_analysis)

                    elif submenu_choice == 3:
                        results = diagnostic_service.lookup_ip_addresses()
                        output.ipv_check_results_output(results)

            elif choice == 3:
                while True:
                    output.mainmenu4()
                    submenu_choice = read_menu_choice(3)

                    if submenu_choice == 3:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    report = diagnostic_service.get_listening_ports()

                    if submenu_choice == 1:
                        output.display_open_ports_header()
                        output.display_open_ports_count(report.counts)
                        output.display_open_ports(report.sockets)

                    elif submenu_choice == 2:
                        output.display_known_services(report.services)

            elif choice == 4:
                while True:
                    output.mainmenu5()
                    submenu_choice = read_menu_choice(3)

                    if submenu_choice == 3:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    if submenu_choice == 1:
                        snapshot = diagnostic_service.get_routing_snapshot()

                        output.display_routing_table(
                            snapshot.hostname,
                            snapshot.interface,
                            snapshot.ip_address,
                            snapshot.gateway,
                            snapshot.routes
                        )
                        display_errors(snapshot.errors.values())

                    elif submenu_choice == 2:
                        interface_data = diagnostic_service.get_network_interfaces()
                        output.display_network_interface_status(interface_data)

        except DiagnosticError as error:
            output.display_command_error(str(error))


def run():
    """Command-line entry point used by network-diagnostic and python -m."""
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass

    output.display_exit_message()
