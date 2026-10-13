import analysis
import dns
import network
import output
import ping


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


def main():
    output.clear_screen()

    while True:
        try:
            try:
                network_connected = network.get_network_plugin()
            except network.CommandError as error:
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
                    ping.handle_ping(targets[submenu_choice])

            elif choice == 2:
                while True:
                    output.mainmenu3()
                    submenu_choice = read_menu_choice(4)

                    if submenu_choice == 4:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    if submenu_choice == 1:
                        result = dns.dns_query("google.com")
                        output.dns_query_output(result)

                    elif submenu_choice == 2:
                        result = dns.dns_latency("google.com")

                        if result["dns_latency_ms"] is not None:
                            result["analysis"] = (
                                analysis.dns_latency_analyzation(result)
                            )

                        output.dns_latency_output(result)

                    elif submenu_choice == 3:
                        sites = [
                            "google.com",
                            "wikipedia.com",
                            "example.com",
                            "yle.fi"
                        ]

                        results = [
                            dns.ipv_check(site)
                            for site in sites
                        ]

                        output.ipv_check_results_output(results)

            elif choice == 3:
                while True:
                    output.mainmenu4()
                    submenu_choice = read_menu_choice(3)

                    if submenu_choice == 3:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    raw = network.get_listening_sockets()
                    data = network.parse_ss_output(raw)
                    open_port_data = analysis.get_open_ports(data)

                    if submenu_choice == 1:
                        counts = analysis.count_open_ports(open_port_data)

                        output.display_open_ports_header()
                        output.display_open_ports_count(counts)
                        output.display_open_ports(open_port_data)

                    elif submenu_choice == 2:
                        services = analysis.analyze_open_ports(open_port_data)
                        output.display_known_services(services)

            elif choice == 4:
                while True:
                    output.mainmenu5()
                    submenu_choice = read_menu_choice(3)

                    if submenu_choice == 3:
                        output.clear_screen()
                        break

                    output.clear_screen()

                    if submenu_choice == 1:
                        route_data = network.get_ip_route()

                        output.display_routing_table(
                            network.get_hostname(),
                            network.get_nic(route_data),
                            network.get_ip(route_data),
                            network.get_gateway(route_data),
                            route_data
                        )

                    elif submenu_choice == 2:
                        results = network.nmcli_status()
                        interface_data = network.parse_nmcli_output(results)

                        output.display_network_interface_status(interface_data)

        except network.CommandError as error:
            output.display_command_error(str(error))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass

    output.display_exit_message()
