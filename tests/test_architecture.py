import importlib
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


@pytest.mark.parametrize("scenario", [
    "success", "total_loss", "technical_failure", "timeout",
])
def test_engine_imports_and_ping_diagnostic_work_without_ui_modules(
    scenario, monkeypatch, subprocess_run, system_command, capsys,
):
    import subprocess

    engine_names = [
        "models", "exceptions", "validation", "parsers", "command_runner", "network", "analysis",
        "dns", "ping",
    ]
    read_input = Mock(side_effect=AssertionError("Engine must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    fixture_name = "total_loss.txt" if scenario == "total_loss" else "success.txt"
    raw = (Path(__file__).parent / "fixtures" / "ping" / fixture_name).read_text(encoding="utf-8")
    subprocess_run.side_effect = None
    code = 1 if scenario == "total_loss" else 0
    subprocess_run.return_value = subprocess.CompletedProcess([], code, raw, "")
    if scenario == "technical_failure":
        subprocess_run.return_value = subprocess.CompletedProcess([], 2, raw, "permission denied")
    elif scenario == "timeout":
        subprocess_run.side_effect = subprocess.TimeoutExpired(["ping"], 10, output=raw.encode())

    # Restore the original module cache afterwards, including original model types.
    with patch.dict(sys.modules):
        for name in engine_names:
            sys.modules.pop(name, None)
        sys.modules["main"] = None
        sys.modules["output"] = None

        modules = {name: importlib.import_module(name) for name in engine_names}

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
        assert sys.modules["main"] is None
        assert sys.modules["output"] is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    system_command.assert_not_called()


def test_engine_rejects_invalid_input_without_commands_output_or_ui_modules(
    monkeypatch, subprocess_run, system_command, capsys,
):
    import subprocess

    engine_names = [
        "models", "exceptions", "validation", "parsers", "command_runner", "network", "analysis",
        "dns", "ping",
    ]
    read_input = Mock(side_effect=AssertionError("Engine must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    subprocess_run.side_effect = None
    subprocess_run.return_value = subprocess.CompletedProcess([], 0, "default via -f dev enp3s0\n", "")

    with patch.dict(sys.modules):
        for name in engine_names:
            sys.modules.pop(name, None)
        sys.modules["main"] = None
        sys.modules["output"] = None

        modules = {name: importlib.import_module(name) for name in engine_names}
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
        assert sys.modules["main"] is None
        assert sys.modules["output"] is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    assert subprocess_run.call_args.args[0] == ["ip", "-4", "route"]
    system_command.assert_not_called()


def test_parsers_are_pure_and_independent_of_commands_and_ui(subprocess_run, system_command, monkeypatch, capsys):
    import ast

    source = (Path(__file__).parent.parent / "parsers.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    read_input = Mock(side_effect=AssertionError("Parsers must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    import parsers

    raw = (Path(__file__).parent / "fixtures" / "ping" / "success.txt").read_text(encoding="utf-8")
    parsers.parse_ping_statistics(raw, "internet")
    with pytest.raises(parsers.ParsingError):
        parsers.parse_ping_statistics("", "internet")

    assert imported == {"ipaddress", "re", "typing", "exceptions", "models", "validation"}
    subprocess_run.assert_not_called()
    system_command.assert_not_called()
    read_input.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


ROOT = Path(__file__).parent.parent
ENGINE_NAMES = [
    "models", "exceptions", "validation", "parsers", "command_runner", "network", "analysis",
    "dns", "ping",
]


def imported_modules(file_name):
    import ast

    imported = set()
    tree = ast.parse((ROOT / file_name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    return imported


def test_service_layer_depends_only_on_engine_modules():
    imported = imported_modules("diagnostic_service.py")

    assert imported <= set(ENGINE_NAMES) | {"typing"}
    for forbidden in ("main", "output", "PySide6", "subprocess", "os", "command_runner"):
        assert forbidden not in imported


def test_service_layer_does_not_print_prompt_or_run_processes():
    import ast

    tree = ast.parse((ROOT / "diagnostic_service.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert not {"print", "input", "exec", "eval"} & names
    assert not {"run", "Popen", "system", "check_output"} & attributes
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))


@pytest.mark.parametrize("name", ENGINE_NAMES)
def test_engine_does_not_depend_on_service_layer_or_cli(name):
    imported = imported_modules(f"{name}.py")

    assert "diagnostic_service" not in imported
    assert "main" not in imported
    assert "output" not in imported


def test_cli_uses_service_layer_instead_of_engine_modules():
    imported = imported_modules("main.py")

    assert imported == {"diagnostic_service", "output", "exceptions"}


def test_service_layer_import_runs_no_commands_and_works_without_ui_modules(
    monkeypatch, subprocess_run, system_command, capsys,
):
    read_input = Mock(side_effect=AssertionError("Service layer must not request input"))
    monkeypatch.setattr("builtins.input", read_input)
    subprocess_run.side_effect = None
    subprocess_run.return_value = __import__("subprocess").CompletedProcess(
        [], 0, "enp3s0:connected\nlo:connected (externally)\n", "",
    )

    with patch.dict(sys.modules):
        for name in ENGINE_NAMES + ["diagnostic_service"]:
            sys.modules.pop(name, None)
        for name in ("main", "output", "PySide6"):
            sys.modules[name] = None

        service = importlib.import_module("diagnostic_service")

        subprocess_run.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

        assert service.get_connection_status() is True
        with pytest.raises(sys.modules["exceptions"].ValidationError):
            service.lookup_ip_addresses(["-f"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    read_input.assert_not_called()
    subprocess_run.assert_called_once()
    system_command.assert_not_called()
