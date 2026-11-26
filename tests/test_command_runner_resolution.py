"""Tests of resolve_trusted_executable on temporary directories.

The real resolver is never mocked here. The temporary tree is owned by the
current user, so tests pass trusted_uids=(os.getuid(),) and stop_at=tmp_path
(the chain check ends there). Defaults are checked separately.
"""

import os
import stat
import subprocess

import pytest

from network_diagnostic.core import command_runner
from network_diagnostic.core.exceptions import (
    CommandNotFoundError, CommandPermissionError, CommandUntrustedError, ValidationError,
)

OWN_UID = os.getuid()
RUNNING_AS_ROOT = OWN_UID == 0


def make_directory(path, mode=0o755):
    """Create path; every new directory below it gets mode 0o755 regardless of the umask."""
    missing = [path, *path.parents]
    missing = [directory for directory in missing if not directory.exists()]
    path.mkdir(parents=True, exist_ok=True)
    for directory in missing:
        directory.chmod(0o755)
    path.chmod(mode)
    return path


def make_executable(directory, name="ip", mode=0o755):
    file = directory / name
    file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    file.chmod(mode)
    return file


@pytest.fixture
def root(tmp_path):
    return make_directory(tmp_path / "root")


def resolve(tmp_path, directories, name="ip", **options):
    options.setdefault("trusted_uids", (OWN_UID,))
    return command_runner.resolve_trusted_executable(
        name, tuple(str(directory) for directory in directories), stop_at=str(tmp_path), **options,
    )


def test_accepts_a_trusted_executable_and_returns_its_real_path(tmp_path, root):
    binaries = make_directory(root / "usr" / "bin")
    file = make_executable(binaries)

    assert resolve(tmp_path, [binaries]) == str(file.resolve())


def test_first_trusted_directory_in_order_wins(tmp_path, root):
    first = make_directory(root / "first")
    second = make_directory(root / "second")
    expected = make_executable(first)
    make_executable(second)

    assert resolve(tmp_path, [first, second]) == str(expected)
    assert resolve(tmp_path, [second, first]) == str(second / "ip")


def test_missing_command_raises_not_found_without_call_context(tmp_path, root):
    binaries = make_directory(root / "bin")

    with pytest.raises(CommandNotFoundError) as caught:
        resolve(tmp_path, [binaries, root / "does-not-exist"])

    error = caught.value
    assert type(error) is CommandNotFoundError
    assert str(error) == "Required command not found: ip"
    assert "trusted directories" in error.reason
    assert error.arguments == []
    assert error.timeout is None


@pytest.mark.parametrize("mode", [0o757, 0o777, 0o775, 0o772])
def test_rejects_group_or_world_writable_file(tmp_path, root, mode):
    binaries = make_directory(root / "bin")
    make_executable(binaries, mode=mode)

    with pytest.raises(CommandUntrustedError, match="^Refusing to run ip: ") as caught:
        resolve(tmp_path, [binaries])

    assert isinstance(caught.value, CommandNotFoundError)


def test_rejects_file_with_an_untrusted_owner(tmp_path, root):
    binaries = make_directory(root / "bin")
    make_executable(binaries)

    with pytest.raises(CommandUntrustedError, match="owned by a trusted user"):
        resolve(tmp_path, [binaries], trusted_uids=(OWN_UID + 1,))


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757])
def test_rejects_writable_directory_containing_the_file(tmp_path, root, mode):
    binaries = make_directory(root / "bin")
    make_executable(binaries)
    binaries.chmod(mode)

    with pytest.raises(CommandUntrustedError, match="writable by group or others"):
        resolve(tmp_path, [binaries])


def test_rejects_writable_directory_higher_in_the_chain(tmp_path, root):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries)
    (root / "usr").chmod(0o777)

    with pytest.raises(CommandUntrustedError, match="writable by group or others"):
        resolve(tmp_path, [binaries])


def test_rejects_directory_chain_with_an_untrusted_owner_at_any_level(tmp_path, root):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries)
    real = os.stat

    def stat_with_foreign_owner(path, *arguments, **options):
        result = real(path, *arguments, **options)
        if str(path) == str(root / "usr"):
            fields = list(result)
            fields[stat.ST_UID] = OWN_UID + 1
            return os.stat_result(fields)
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "stat", stat_with_foreign_owner)
        with pytest.raises(CommandUntrustedError, match="not owned by a trusted user"):
            resolve(tmp_path, [binaries])


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_rejects_non_regular_files(tmp_path, root, kind):
    binaries = make_directory(root / "bin")
    if kind == "directory":
        make_directory(binaries / "ip")
    else:
        os.mkfifo(binaries / "ip", 0o755)

    with pytest.raises(CommandUntrustedError, match="not a regular file"):
        resolve(tmp_path, [binaries])


def test_file_without_any_execute_bit_is_a_permission_error_not_an_untrusted_one(tmp_path, root):
    binaries = make_directory(root / "bin")
    make_executable(binaries, mode=0o600)

    with pytest.raises(CommandPermissionError, match="^Could not execute ip: ") as caught:
        resolve(tmp_path, [binaries])

    assert not isinstance(caught.value, CommandNotFoundError)


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root can search every directory")
def test_directory_that_cannot_be_searched_is_a_permission_error(tmp_path, root):
    binaries = make_directory(root / "bin")
    make_executable(binaries)
    binaries.chmod(0o600)

    try:
        with pytest.raises(CommandPermissionError):
            resolve(tmp_path, [binaries])
    finally:
        binaries.chmod(0o755)


def test_untrusted_earlier_directory_is_skipped_when_a_later_one_is_trusted(tmp_path, root):
    unsafe = make_directory(root / "unsafe")
    make_executable(unsafe, mode=0o777)
    safe = make_directory(root / "safe")
    expected = make_executable(safe)

    assert resolve(tmp_path, [unsafe, safe]) == str(expected)


def test_permission_error_wins_over_untrusted_when_no_directory_is_trusted(tmp_path, root):
    unsafe = make_directory(root / "unsafe")
    make_executable(unsafe, mode=0o777)
    unusable = make_directory(root / "unusable")
    make_executable(unusable, mode=0o600)

    with pytest.raises(CommandPermissionError):
        resolve(tmp_path, [unsafe, unusable])


def test_symlink_to_an_untrusted_target_is_rejected(tmp_path, root):
    binaries = make_directory(root / "bin")
    outside = make_directory(root / "outside")
    target = make_executable(outside, "payload", mode=0o777)
    (binaries / "ip").symlink_to(target)

    with pytest.raises(CommandUntrustedError):
        resolve(tmp_path, [binaries])


def test_symlink_to_a_target_in_an_untrusted_directory_is_rejected(tmp_path, root):
    binaries = make_directory(root / "bin")
    outside = make_directory(root / "outside", mode=0o777)
    target = make_executable(outside, "payload")
    (binaries / "ip").symlink_to(target)

    with pytest.raises(CommandUntrustedError, match="writable by group or others"):
        resolve(tmp_path, [binaries])


def test_symlinked_directory_resolves_to_the_real_file_without_duplicates(tmp_path, root):
    # Layout like a merged-usr system: bin -> usr/bin.
    binaries = make_directory(root / "usr" / "bin")
    expected = make_executable(binaries)
    (root / "bin").symlink_to("usr/bin")

    assert resolve(tmp_path, [root / "bin", binaries]) == str(expected)
    assert resolve(tmp_path, [binaries, root / "bin"]) == str(expected)


def test_symlink_chain_through_trusted_directories_returns_the_final_file(tmp_path, root):
    # sbin/ip -> ../bin/ip -> ../usr/bin/ip
    final_directory = make_directory(root / "usr" / "bin")
    final = make_executable(final_directory)
    middle = make_directory(root / "bin")
    (middle / "ip").symlink_to("../usr/bin/ip")
    first = make_directory(root / "sbin")
    (first / "ip").symlink_to("../bin/ip")

    assert resolve(tmp_path, [first]) == str(final)


def test_symlink_chain_with_a_writable_intermediate_directory_is_rejected(tmp_path, root):
    final_directory = make_directory(root / "usr" / "bin")
    make_executable(final_directory)
    middle = make_directory(root / "bin")
    (middle / "ip").symlink_to("../usr/bin/ip")
    first = make_directory(root / "sbin")
    (first / "ip").symlink_to("../bin/ip")
    middle.chmod(0o777)

    with pytest.raises(CommandUntrustedError, match="writable by group or others"):
        resolve(tmp_path, [first])


def test_absolute_symlink_target_is_followed(tmp_path, root):
    binaries = make_directory(root / "usr" / "bin")
    final = make_executable(binaries)
    links = make_directory(root / "links")
    (links / "ip").symlink_to(final)

    assert resolve(tmp_path, [links]) == str(final)


def test_too_many_symlinks_and_symlink_loops_are_rejected(tmp_path, root):
    binaries = make_directory(root / "bin")
    final = make_executable(make_directory(root / "usr"), "real")
    count = command_runner.MAX_SYMLINKS + 4
    (binaries / f"link{count}").symlink_to(final)
    for number in range(count - 1, -1, -1):
        (binaries / f"link{number}").symlink_to(f"link{number + 1}")
    (binaries / "ip").symlink_to("link0")
    loops = make_directory(root / "loops")
    (loops / "ip").symlink_to("other")
    (loops / "other").symlink_to("ip")

    with pytest.raises(CommandUntrustedError, match="symbolic links"):
        resolve(tmp_path, [binaries])
    with pytest.raises(CommandUntrustedError):
        resolve(tmp_path, [loops])


def test_symlink_chain_at_the_limit_is_accepted(tmp_path, root):
    binaries = make_directory(root / "bin")
    final = make_executable(make_directory(root / "usr"), "real")
    count = command_runner.MAX_SYMLINKS - 1  # ip and link1..link15 are 16 links in total
    (binaries / f"link{count}").symlink_to(final)
    for number in range(count - 1, 0, -1):
        (binaries / f"link{number}").symlink_to(f"link{number + 1}")
    (binaries / "ip").symlink_to("link1")

    assert resolve(tmp_path, [binaries]) == str(final)


def test_dangling_symlink_is_not_trusted(tmp_path, root):
    binaries = make_directory(root / "bin")
    (binaries / "ip").symlink_to(root / "missing")

    with pytest.raises(CommandUntrustedError):
        resolve(tmp_path, [binaries])


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="files created by root are trusted by default")
def test_default_trusted_owner_is_root_only(tmp_path, root):
    binaries = make_directory(root / "bin")
    make_executable(binaries)

    assert command_runner.TRUSTED_OWNER_UIDS == (0,)
    with pytest.raises(CommandUntrustedError):
        command_runner.resolve_trusted_executable("ip", (str(binaries),), stop_at=str(tmp_path))


def pretend_owner(monkeypatch, uid):
    """Make every stat() result report the given owner without touching the file system."""
    def with_owner(real):
        def wrapper(path, *arguments, **options):
            result = real(path, *arguments, **options)
            fields = list(result)
            fields[stat.ST_UID] = uid
            return os.stat_result(fields)
        return wrapper

    monkeypatch.setattr(os, "stat", with_owner(os.stat))
    monkeypatch.setattr(os, "lstat", with_owner(os.lstat))


def test_overflow_uid_nobody_is_not_trusted_by_default(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    make_executable(binaries)
    pretend_owner(monkeypatch, 65534)

    with pytest.raises(CommandUntrustedError, match="not owned by a trusted user"):
        command_runner.resolve_trusted_executable("ip", (str(binaries),), stop_at=str(tmp_path))


def test_uid_zero_is_trusted_by_default(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    expected = make_executable(binaries)
    pretend_owner(monkeypatch, 0)

    result = command_runner.resolve_trusted_executable("ip", (str(binaries),), stop_at=str(tmp_path))

    assert result == str(expected)


def test_environment_path_is_never_used_to_find_commands(tmp_path, root, monkeypatch):
    evil = make_directory(root / "evil")
    make_executable(evil, "hostname")
    good = make_directory(root / "usr" / "bin")
    real = make_executable(good, "hostname")
    link_directory = make_directory(root / "links")
    (link_directory / "hostname").symlink_to(real)
    monkeypatch.setenv("PATH", f"{evil}:.")
    monkeypatch.chdir(evil)
    monkeypatch.setattr(
        command_runner, "_resolve_executable",
        lambda name: resolve(tmp_path, [link_directory], name=name),
    )
    run = []

    def fake_run_bounded(arguments, **options):
        run.append((arguments, options))
        return subprocess.CompletedProcess(arguments, 0, "host\n", "")

    monkeypatch.setattr(command_runner, "_run_bounded", fake_run_bounded)

    command_runner.run_command(["hostname"])

    arguments, options = run[0]
    assert arguments == ["hostname"]
    assert options["executable"] == str(real)
    assert str(evil) not in options["env"]["PATH"]


@pytest.mark.skipif(
    os.stat("/usr/bin").st_uid != 0, reason="system files not owned by uid 0 (user namespace)",
)
def test_real_system_resolution_finds_hostname_in_a_trusted_directory():
    result = command_runner.resolve_trusted_executable("hostname")

    assert os.path.isabs(result)
    assert os.path.isfile(result)
    assert os.access(result, os.X_OK)
    assert result == os.path.realpath(result)


@pytest.mark.skipif(
    os.stat("/usr/bin").st_uid != 0, reason="system files not owned by uid 0 (user namespace)",
)
def test_real_system_resolution_reports_a_missing_command():
    with pytest.raises(CommandNotFoundError) as caught:
        command_runner.resolve_trusted_executable("no-such-diagnostic-command")

    assert type(caught.value) is CommandNotFoundError


# --- file system errors during the checks ----------------------------------

def failing_stat(monkeypatch, target, failure=None, mode_change=None):
    """Make os.stat fail (or report a different type) for one exact path."""
    real = os.stat

    def wrapper(path, *arguments, **options):
        if str(path) == str(target):
            if failure is not None:
                raise failure
            fields = list(real(path, *arguments, **options))
            fields[stat.ST_MODE] = mode_change
            return os.stat_result(fields)
        return real(path, *arguments, **options)

    monkeypatch.setattr(os, "stat", wrapper)


def test_unreadable_directory_in_the_chain_is_a_permission_error(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries)
    failing_stat(monkeypatch, root / "usr", failure=PermissionError(13, "Permission denied"))

    with pytest.raises(CommandPermissionError, match="Permission denied"):
        resolve(tmp_path, [binaries])


def test_directory_in_the_chain_that_cannot_be_inspected_is_untrusted(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries)
    failing_stat(monkeypatch, root / "usr", failure=OSError(5, "Input/output error"))

    with pytest.raises(CommandUntrustedError, match="Input/output error"):
        resolve(tmp_path, [binaries])


def test_chain_entry_that_is_not_a_directory_is_untrusted(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries)
    failing_stat(monkeypatch, root / "usr", mode_change=stat.S_IFREG | 0o755)

    with pytest.raises(CommandUntrustedError, match="not a directory"):
        resolve(tmp_path, [binaries])


def test_unreadable_file_status_is_a_permission_error_and_other_errors_are_untrusted(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    file = make_executable(binaries)
    failing_stat(monkeypatch, file, failure=PermissionError(13, "Permission denied"))
    with pytest.raises(CommandPermissionError):
        resolve(tmp_path, [binaries])

    failing_stat(monkeypatch, file, failure=OSError(5, "Input/output error"))
    with pytest.raises(CommandUntrustedError):
        resolve(tmp_path, [binaries])


def test_candidate_that_cannot_be_looked_up_is_untrusted_or_a_permission_error(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    file = make_executable(binaries)
    real = os.lstat

    def failing_lstat(failure):
        def wrapper(path, *arguments, **options):
            if str(path) == str(file):
                raise failure
            return real(path, *arguments, **options)
        return wrapper

    monkeypatch.setattr(os, "lstat", failing_lstat(OSError(40, "Too many levels of symbolic links")))
    with pytest.raises(CommandUntrustedError, match="symbolic links"):
        resolve(tmp_path, [binaries])

    monkeypatch.setattr(os, "lstat", failing_lstat(PermissionError(13, "Permission denied")))
    with pytest.raises(CommandPermissionError):
        resolve(tmp_path, [binaries])


def test_symlink_that_cannot_be_read_is_untrusted(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    final = make_executable(make_directory(root / "usr"), "real")
    (binaries / "ip").symlink_to(final)
    monkeypatch.setattr(os, "readlink", lambda path: (_ for _ in ()).throw(OSError(5, "Input/output error")))

    with pytest.raises(CommandUntrustedError, match="cannot follow"):
        resolve(tmp_path, [binaries])


def test_same_real_directory_is_checked_only_once(tmp_path, root):
    binaries = make_directory(root / "usr" / "bin")
    make_executable(binaries, mode=0o777)
    (root / "bin").symlink_to("usr/bin")

    with pytest.raises(CommandUntrustedError):
        resolve(tmp_path, [root / "bin", binaries])


def test_directory_chain_check_walks_up_to_the_file_system_root_without_stop_at(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    expected = make_executable(binaries)
    real = os.stat

    def trusted_tree(path, *arguments, **options):
        fields = list(real(path, *arguments, **options))
        fields[stat.ST_UID] = 0
        fields[stat.ST_MODE] &= ~(stat.S_IWGRP | stat.S_IWOTH)
        return os.stat_result(fields)

    monkeypatch.setattr(os, "stat", trusted_tree)

    assert command_runner.resolve_trusted_executable("ip", (str(binaries),)) == str(expected)


def test_resolver_refuses_names_that_are_not_bare_command_names(tmp_path, root):
    binaries = make_directory(root / "bin")

    for name in ("", "/bin/ip", "../ip", "a/b", ".", ".."):
        with pytest.raises(ValidationError):
            resolve(tmp_path, [binaries], name=name)


def test_none_arguments_select_the_trusted_directories_and_the_root_owner():
    with pytest.raises(CommandNotFoundError) as caught:
        command_runner.resolve_trusted_executable("no-such-diagnostic-command", None, trusted_uids=None)

    for directory in command_runner.TRUSTED_DIRECTORIES:
        assert directory in caught.value.reason


def test_trusted_directories_are_read_at_call_time(tmp_path, root, monkeypatch):
    binaries = make_directory(root / "bin")
    expected = make_executable(binaries)
    monkeypatch.setattr(command_runner, "TRUSTED_DIRECTORIES", (str(binaries),))

    result = command_runner.resolve_trusted_executable("ip", trusted_uids=(OWN_UID,), stop_at=str(tmp_path))

    assert result == str(expected)
