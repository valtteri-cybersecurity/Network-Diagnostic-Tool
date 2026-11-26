import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import network_diagnostic

# Source files are read from the installed package, not from the repository.
PACKAGE_DIR = Path(network_diagnostic.__file__).parent
FIXTURES = Path(__file__).parent / "fixtures"

CORE = "network_diagnostic.core"
SERVICE = "network_diagnostic.diagnostic_service"
CLI = "network_diagnostic.cli"
ENGINE_NAMES = [
    "models", "exceptions", "validation", "parsers", "command_runner", "network", "analysis",
    "dns", "ping", "snapshot_serialization",
]
ENGINE_MODULES = [f"{CORE}.{name}" for name in ENGINE_NAMES]
UI_MODULES = [CLI, f"{CLI}.main", f"{CLI}.output", "network_diagnostic.__main__", "PySide6"]


def package_modules():
    """Return {module name: source path} for every module in the package."""
    modules = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        parts = list(path.relative_to(PACKAGE_DIR.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    return modules


def imported_modules(module_name):
    """Return the full names of all modules imported by one package module."""
    known = package_modules()
    imported = set()
    tree = ast.parse(known[module_name].read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{module_name} uses a relative import"
            for alias in node.names:
                submodule = f"{node.module}.{alias.name}"
                if submodule in known:
                    imported.add(submodule)
                else:
                    imported.add(node.module)
    return imported


def fresh_package_modules():
    """Remove the package from the module cache so that imports run again.

    Use inside patch.dict(sys.modules), which restores the original modules.
    """
    for name in list(sys.modules):
        if name == "network_diagnostic" or name.startswith("network_diagnostic."):
            del sys.modules[name]


def use_shared_seam(runner_module, seam):
    """Give a freshly imported command_runner the guarded seams that conftest.py gave the original.

    conftest.py patches the module object imported at collection time. A module
    imported again inside patch.dict(sys.modules) is a new object and would
    resolve and start real commands without this.
    """
    runner_module._run_bounded = seam
    runner_module._resolve_executable = lambda name: f"/usr/bin/{name}"


def block_ui_modules():
    for name in UI_MODULES:
        sys.modules[name] = None


def test_package_contains_expected_modules():
    expected = {
        "network_diagnostic", "network_diagnostic.__main__", SERVICE,
        CORE, *ENGINE_MODULES,
        CLI, f"{CLI}.main", f"{CLI}.output",
    }

    assert set(package_modules()) == expected


@pytest.mark.parametrize("scenario", [
    "success", "total_loss", "technical_failure", "timeout",
])
def test_engine_imports_and_ping_diagnostic_work_without_ui_modules(
    scenario, monkeypatch, subprocess_run, system_command, capsys,
):
    read_input = Mock(side_effect=AssertionError("Engine must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    fixture_name = "total_loss.txt" if scenario == "total_loss" else "success.txt"
    raw = (FIXTURES / "ping" / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    code = 1 if scenario == "total_loss" else 0
    subprocess_run.return_value = subprocess.CompletedProcess([], code, raw, "")
    if scenario == "technical_failure":
        subprocess_run.return_value = subprocess.CompletedProcess([], 2, raw, "permission denied")
    elif scenario == "timeout":
        subprocess_run.side_effect = subprocess.TimeoutExpired(["ping"], 10, output=raw.encode())

    # Restore the original module cache afterwards, including original model types.
    with patch.dict(sys.modules):
        fresh_package_modules()
        block_ui_modules()
        sys.modules[SERVICE] = None

        modules = {name: importlib.import_module(f"{CORE}.{name}") for name in ENGINE_NAMES}
        use_shared_seam(modules["command_runner"], subprocess_run)

        subprocess_run.assert_not_called()
        system_command.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

        result = modules["ping"].run_ping_diagnostic("internet")

        models = modules["models"]
        assert isinstance(result, models.PingResult)
        assert result.raw_output == raw
        if scenario == "success":
            assert result.execution_status is models.ExecutionStatus.COMPLETED
            assert result.reachable is True
            assert result.packet_loss_status is models.PacketLossStatus.NO_LOSS
            assert result.latency_analysis == "OK"
            assert result.rtt_range_analysis == "Good"
        elif scenario == "total_loss":
            assert result.execution_status is models.ExecutionStatus.COMPLETED
            assert result.reachable is False
            assert result.packet_loss_status is models.PacketLossStatus.TOTAL_LOSS
            assert result.latency_analysis is None
            assert result.rtt_range_analysis is None
        else:
            expected = models.ExecutionStatus.TIMEOUT if scenario == "timeout" else models.ExecutionStatus.FAILED
            assert result.execution_status is expected
            assert result.reachable is None
            assert result.packets_received is None
            assert result.latency_analysis is None
            assert result.rtt_range_analysis is None
            assert result.error == ("ping timed out after 10 seconds." if scenario == "timeout" else "ping failed: permission denied")
        assert sys.modules[f"{CLI}.main"] is None
        assert sys.modules[f"{CLI}.output"] is None
        assert sys.modules[SERVICE] is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    system_command.assert_not_called()


def test_engine_rejects_invalid_input_without_commands_output_or_ui_modules(
    monkeypatch, subprocess_run, system_command, capsys,
):
    read_input = Mock(side_effect=AssertionError("Engine must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "default via -f dev enp3s0\n", "")

    with patch.dict(sys.modules):
        fresh_package_modules()
        block_ui_modules()
        sys.modules[SERVICE] = None

        modules = {name: importlib.import_module(f"{CORE}.{name}") for name in ENGINE_NAMES}
        use_shared_seam(modules["command_runner"], subprocess_run)
        validation_error = modules["exceptions"].ValidationError

        assert issubclass(validation_error, modules["exceptions"].DiagnosticError)
        assert not issubclass(validation_error, modules["exceptions"].CommandError)
        with pytest.raises(validation_error):
            modules["dns"].dns_latency("+trace")
        with pytest.raises(validation_error):
            modules["command_runner"].run_command(["ping", "127.0.0.1\0"])
        subprocess_run.assert_not_called()

        result = modules["ping"].run_ping_diagnostic("gateway")

        assert isinstance(result, modules["models"].PingResult)
        assert result.execution_status is modules["models"].ExecutionStatus.FAILED
        assert result.error == "Invalid default gateway: Invalid IPv4 address: '-f'"
        assert sys.modules[f"{CLI}.main"] is None
        assert sys.modules[f"{CLI}.output"] is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["ip", "-4", "route"]
    system_command.assert_not_called()


def test_parsers_are_pure_and_independent_of_commands_and_ui(subprocess_run, system_command, monkeypatch, capsys):
    imported = imported_modules(f"{CORE}.parsers")
    read_input = Mock(side_effect=AssertionError("Parsers must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    from network_diagnostic.core import parsers

    raw = (FIXTURES / "ping" / "success.txt").read_text(encoding="utf-8")
    parsers.parse_ping_statistics(raw, "internet")
    with pytest.raises(parsers.ParsingError):
        parsers.parse_ping_statistics("", "internet")

    assert imported == {
        "ipaddress", "math", "re", "typing",
        f"{CORE}.exceptions", f"{CORE}.models", f"{CORE}.validation",
    }
    subprocess_run.assert_not_called()
    system_command.assert_not_called()
    read_input.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_service_layer_depends_only_on_engine_modules():
    imported = imported_modules(SERVICE)

    assert imported <= set(ENGINE_MODULES) | {"typing", "datetime", "re", "sys", "time", "uuid"}
    for forbidden in (
        CLI, f"{CLI}.main", f"{CLI}.output", "PySide6", "subprocess", "os", f"{CORE}.command_runner",
    ):
        assert forbidden not in imported


def test_service_layer_does_not_print_prompt_or_run_processes():
    tree = ast.parse(package_modules()[SERVICE].read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert not {"print", "input", "exec", "eval"} & names
    assert not {"run", "Popen", "system", "check_output"} & attributes
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))


RUNNER = f"{CORE}.command_runner"
FORBIDDEN_PROCESS_CALLS = {
    "os": {"system", "popen", "execv", "execve", "execvp", "execl", "execlp", "spawnv", "spawnl", "fork", "posix_spawn"},
    "subprocess": {"run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"},
}


def calls(module_name):
    tree = ast.parse(package_modules()[module_name].read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def attribute_path(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_only_the_command_runner_imports_subprocess():
    for name in package_modules():
        imported = imported_modules(name)
        if name == RUNNER:
            assert "subprocess" in imported
        else:
            assert "subprocess" not in imported, f"{name} imports subprocess"


@pytest.mark.parametrize("name", [module for module in package_modules() if module.startswith(CORE)])
def test_core_sources_start_processes_only_through_the_hardened_runner(name):
    for call in calls(name):
        assert attribute_path(call.func) not in {
            f"{module}.{function}" for module, functions in FORBIDDEN_PROCESS_CALLS.items() for function in functions
        }, f"{name} line {call.lineno} starts a process outside the runner"
        for keyword in call.keywords:
            assert not (
                keyword.arg == "shell" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
            ), f"{name} line {call.lineno} does not hardcode shell=False"
    if name != RUNNER:
        assert not [call for call in calls(name) if attribute_path(call.func).endswith("Popen")]


def test_runner_starts_one_process_with_fixed_safe_options():
    popen_calls = [call for call in calls(RUNNER) if attribute_path(call.func) == "subprocess.Popen"]

    assert len(popen_calls) == 1
    options = {keyword.arg: keyword.value for keyword in popen_calls[0].keywords}
    assert "preexec_fn" not in options
    assert None not in options, "Popen options must not be passed through **kwargs"
    for name, expected in {
        "shell": False, "close_fds": True, "start_new_session": True, "cwd": "/", "bufsize": 0,
    }.items():
        assert isinstance(options[name], ast.Constant) and options[name].value == expected, name
    assert attribute_path(options["stdin"]) == "subprocess.DEVNULL"
    assert attribute_path(options["stdout"]) == "subprocess.PIPE"
    assert attribute_path(options["stderr"]) == "subprocess.PIPE"
    assert "env" in options and "executable" in options
    assert not [call for call in popen_calls[0].args if isinstance(call, ast.Starred)]


def test_trusted_resolution_is_called_only_from_the_default_resolver():
    tree = ast.parse(package_modules()[RUNNER].read_text(encoding="utf-8"))
    callers = set()
    for function in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]:
        if any(
            isinstance(node, ast.Call) and attribute_path(node.func) == "resolve_trusted_executable"
            for node in ast.walk(function)
        ):
            callers.add(function.name)

    assert callers == {"_resolve_executable"}
    for name in package_modules():
        if name != RUNNER:
            assert "resolve_trusted_executable" not in package_modules()[name].read_text(encoding="utf-8")


def test_freshly_imported_runner_cannot_start_real_processes_or_signal_groups(subprocess_run):
    # Meta test for use_shared_seam: without it, a re-imported command_runner would
    # resolve real files (on a root-owned system) and start real commands.
    with patch.dict(sys.modules):
        fresh_package_modules()
        runner = importlib.import_module(RUNNER)

        assert runner._run_bounded is not subprocess_run
        use_shared_seam(runner, subprocess_run)

        assert runner._run_bounded is subprocess_run
        assert runner._resolve_executable("ping") == "/usr/bin/ping"
        with pytest.raises(AssertionError, match="forbidden"):
            runner.run_command(["hostname"])
        with pytest.raises(AssertionError, match="Real processes are forbidden"):
            runner.subprocess.Popen(["hostname"])
        with pytest.raises(AssertionError, match="forbidden"):
            runner.os.killpg(os.getpgrp(), 0)
        subprocess_run.assert_called_once()


def test_runner_never_reads_the_process_environment_or_current_directory():
    tree = ast.parse(package_modules()[RUNNER].read_text(encoding="utf-8"))
    attributes = {attribute_path(node) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert not {"os.environ", "os.getenv", "os.getcwd", "os.putenv"} & attributes


@pytest.mark.parametrize("name", [CORE] + ENGINE_MODULES)
def test_engine_depends_only_on_engine_and_standard_library(name):
    imported = imported_modules(name)

    for module in imported:
        if module.startswith("network_diagnostic"):
            assert module.startswith(CORE + "."), f"{name} imports {module}"
    assert SERVICE not in imported
    assert "PySide6" not in imported


def test_cli_uses_service_layer_instead_of_engine_modules():
    assert imported_modules(f"{CLI}.main") == {SERVICE, f"{CLI}.output", f"{CORE}.exceptions"}
    assert imported_modules(f"{CLI}.output") == {"os", "datetime", "typing", f"{CORE}.models"}


def test_entry_points_and_package_markers_import_only_the_cli_entry():
    assert imported_modules("network_diagnostic.__main__") == {f"{CLI}.main"}
    for name in ("network_diagnostic", CORE, CLI):
        assert imported_modules(name) == set()


def test_package_has_no_circular_imports():
    known = set(package_modules())
    graph = {}
    for name in known:
        graph[name] = {module for module in imported_modules(name) if module in known}

    # Depth-first search: reaching a module that is still on the path is a cycle.
    finished = set()

    def visit(name, path):
        assert name not in path, "Circular import: " + " -> ".join(path + [name])
        if name in finished:
            return
        for module in sorted(graph[name]):
            visit(module, path + [name])
        finished.add(name)

    for name in sorted(graph):
        visit(name, [])


def test_importing_every_package_module_has_no_side_effects(
    monkeypatch, subprocess_run, system_command, capsys,
):
    read_input = Mock(side_effect=AssertionError("Import must not request input"))
    monkeypatch.setattr("builtins.input", read_input)

    with patch.dict(sys.modules):
        fresh_package_modules()

        for name in package_modules():
            importlib.import_module(name)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_not_called()
    system_command.assert_not_called()


def test_service_layer_import_runs_no_commands_and_works_without_ui_modules(
    monkeypatch, subprocess_run, system_command, capsys,
):
    read_input = Mock(side_effect=AssertionError("Service layer must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess(
        [], 0, "enp3s0:connected\nlo:connected (externally)\n", "",
    )

    with patch.dict(sys.modules):
        fresh_package_modules()
        block_ui_modules()

        service = importlib.import_module(SERVICE)
        use_shared_seam(sys.modules[f"{CORE}.command_runner"], subprocess_run)

        subprocess_run.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

        assert service.get_connection_status() is True
        with pytest.raises(sys.modules[f"{CORE}.exceptions"].ValidationError):
            service.lookup_ip_addresses(["-f"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    system_command.assert_not_called()


def test_service_layer_exposes_the_public_functions():
    from network_diagnostic import diagnostic_service

    for name in (
        "get_connection_status", "run_ping_diagnostic", "run_dns_query", "run_dns_latency_test",
        "lookup_ip_addresses", "get_listening_ports", "get_routing_snapshot", "get_network_interfaces",
    ):
        assert callable(getattr(diagnostic_service, name))


def test_snapshot_models_are_data_only_and_independent_of_execution_and_ui():
    imported = imported_modules(f"{CORE}.models")

    # Reuse of the existing validators, without a cycle: neither imports models.
    assert imported == {
        "dataclasses", "datetime", "enum", "ipaddress", "math", "re", "typing", "uuid",
        f"{CORE}.exceptions", f"{CORE}.validation",
    }
    assert {name for name in imported if name.startswith("network_diagnostic")} == {
        f"{CORE}.exceptions", f"{CORE}.validation",
    }
    for validator in ("exceptions", "validation"):
        assert f"{CORE}.models" not in imported_modules(f"{CORE}.{validator}")
    for forbidden in ("subprocess", "os", "socket", "http", "urllib", "ssl", "PySide6", CLI):
        assert forbidden not in imported
    tree = ast.parse(package_modules()[f"{CORE}.models"].read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not {"print", "input", "exec", "eval", "open", "__import__"} & names
    assert not {"run", "Popen", "system", "check_output", "call", "connect"} & attributes


SERIALIZATION = f"{CORE}.snapshot_serialization"


def test_snapshot_serialization_imports_only_models_and_pure_standard_modules():
    imported = imported_modules(SERIALIZATION)

    assert imported == {"datetime", "enum", "math", f"{CORE}.models"}
    for forbidden in ("json", "os", "pathlib", "subprocess", "logging", "socket", "PySide6", CLI, SERVICE):
        assert forbidden not in imported
    assert SERIALIZATION not in imported_modules(SERVICE)
    assert SERIALIZATION not in imported_modules(f"{CORE}.models")


@pytest.mark.parametrize("module", [SERIALIZATION, SERVICE])
def test_snapshot_modules_have_no_file_writes_commands_or_remediation(module):
    tree = ast.parse(package_modules()[module].read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    assert not {"print", "input", "exec", "eval", "open", "__import__", "compile"} & names
    assert not {"run", "Popen", "system", "check_output", "write", "write_text", "write_bytes", "run_command"} & attributes
    assert not {"run_command", "subprocess", "os"} & names
    commands = ("sudo", "systemctl", "nmcli", "ifconfig", "resolvectl", "iptables", "dhclient")
    assert not [text for text in strings if text in commands or text.split(" ")[0] in commands]


def test_service_pre_validation_does_not_drift_from_the_model_rules():
    from network_diagnostic import diagnostic_service
    from network_diagnostic.core import models

    assert diagnostic_service.DEFAULT_PING_TARGETS == models._PING_TARGETS
    assert diagnostic_service._ROUTING_FIELDS == models._ROUTING_FIELDS
    assert diagnostic_service._MAX_TARGETS == 4

    # The model keeps the DNS-target regex inline, so find it in the source.
    tree = ast.parse(package_modules()[f"{CORE}.models"].read_text(encoding="utf-8"))
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert diagnostic_service._HOST_STRUCTURE.pattern in literals


@pytest.mark.parametrize("hostname", [
    "example.com", "a", "a" * 254, "a" * 255, "", "-a", "a-", "a b", "a\n", "a/b", "exämple.com", "A.B-c.d",
    "2001:db8::1", "::", "a_b", "a..b", "+a", "a%eth0", "1.2.3.4", "a:b-c.d",
])
def test_service_host_structure_matches_the_model_dns_target_rule(hostname):
    from datetime import datetime, timezone
    from network_diagnostic import diagnostic_service
    from network_diagnostic.core.models import (
        CollectionResult, CollectionStatus, DnsQueryResult, ExecutionStatus,
    )

    service_accepts = (
        diagnostic_service._HOST_STRUCTURE.fullmatch(hostname) is not None and not hostname.startswith("-")
    )
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    value = DnsQueryResult(hostname=hostname, execution_status=ExecutionStatus.COMPLETED)
    try:
        CollectionResult(
            "dns.query", "system_resolver", hostname, CollectionStatus.COMPLETED, value,
            started_at=moment, finished_at=moment, duration_seconds=0.0,
        )
        model_accepts = True
    except ValueError:
        model_accepts = False

    assert service_accepts is model_accepts
