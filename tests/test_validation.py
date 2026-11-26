import pytest

from network_diagnostic.core.exceptions import CommandError, DiagnosticError, ValidationError
from network_diagnostic.core.validation import (
    validate_command_arguments,
    validate_host,
    validate_interface_name,
    validate_ipv4_address,
    validate_timeout,
)


OPTION_LIKE_VALUES = ["-f", "--help", "+trace", "+time=999", "@192.0.2.1"]
INJECTION_VALUES = [
    "example.com;id", "example.com|id", "example.com&&id", "$(id)", "`id`",
    "example.com>out", "example.com\nid", "example.com\rid", "example.com\0",
    "example.com\t", " example.com", "example.com ", "example\x1b[31m.com",
    "example.com\x7f", "example\u00a0.com", "\u0661\u0662\u0663.example",
]


def test_validation_error_is_diagnostic_error_but_not_command_or_value_error():
    error = ValidationError("rejected")

    assert isinstance(error, DiagnosticError)
    assert not isinstance(error, CommandError)
    assert not isinstance(error, ValueError)
    assert str(error) == "rejected"


@pytest.mark.parametrize("value", [
    "localhost", "google.com", "example.com.", "IN", "CH", "A", "a",
    "xn--bcher-kva.example", "my-host.example", "host1.example", "1host.example",
    "a" * 63 + ".example", "8.8.8.8", "127.0.0.1", "0.0.0.0", "255.255.255.255",
    "::1", "2001:db8::1", "::ffff:192.0.2.1", "FE80::1", "0xdeadbeef.example",
    "3com", "abc.0x7f", "example.123",
])
def test_validate_host_accepts_hostnames_and_ip_literals(value):
    assert validate_host(value) is None


def test_validate_host_accepts_maximum_name_length_with_and_without_trailing_dot():
    name = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
    assert len(name) == 253

    assert validate_host(name) is None
    assert validate_host(name + ".") is None


def test_validate_host_rejects_name_longer_than_253_characters():
    name = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 62])
    assert len(name) == 254

    with pytest.raises(ValidationError):
        validate_host(name)
    with pytest.raises(ValidationError):
        validate_host(name + ".")


@pytest.mark.parametrize("value", [
    "", ".", "..", "example..com", ".example.com", "example.com..",
    "a" * 64 + ".example", "-example.com", "example-.com", "example.-com",
    "under_score.example", "bücher.example", "example.com/path",
    "http://example.com", "example.com:80", "192.0.2.1:80", "192.0.2.0/24",
    "2001:db8::/32", "[::1]", "[2001:db8::1]:53", "fe80::1%eth0", "fe80::1%1",
    "999.1.2.3", "256.256.256.256", "1.2.3", "1.2.3.4.5", "01.2.3.4", "1.2.3.4.",
    "123", "2001:db8:::1", "user@example.com", "example.com!",
])
def test_validate_host_rejects_invalid_hosts(value):
    with pytest.raises(ValidationError):
        validate_host(value)


@pytest.mark.parametrize("value", OPTION_LIKE_VALUES + INJECTION_VALUES)
def test_validate_host_rejects_option_like_and_injection_values(value):
    with pytest.raises(ValidationError):
        validate_host(value)


@pytest.mark.parametrize("value", [None, 1, 8.8, b"example.com", ["example.com"]])
def test_validate_host_rejects_wrong_types(value):
    with pytest.raises(ValidationError, match="host must be a string"):
        validate_host(value)


@pytest.mark.parametrize("value", ["0.0.0.0", "192.0.2.1", "10.0.0.254", "255.255.255.255"])
def test_validate_ipv4_address_accepts_dotted_decimal_addresses(value):
    assert validate_ipv4_address(value) is None


@pytest.mark.parametrize("value", [
    "", "999.1.2.3", "256.0.0.1", "1.2.3", "1.2.3.4.5", "01.2.3.4", "1.2.3.04",
    "1..2.3", "1.2.3.4.", " 1.2.3.4", "1.2.3.4\n", "0x7f.0.0.1", "+1.2.3.4",
    "-1.2.3.4", "1.2.3.4/24", "1.2.3.4:80", "::1", "localhost", "١.٢.٣.٤",
] + OPTION_LIKE_VALUES)
def test_validate_ipv4_address_rejects_invalid_addresses(value):
    with pytest.raises(ValidationError):
        validate_ipv4_address(value)


@pytest.mark.parametrize("value", [None, 3232235521, b"192.0.2.1"])
def test_validate_ipv4_address_rejects_wrong_types(value):
    with pytest.raises(ValidationError, match="IPv4 address must be a string"):
        validate_ipv4_address(value)


@pytest.mark.parametrize("value", [
    "lo", "eth0", "enp3s0", "wlp2s0", "br-1a2b3c", "veth_1", "eth0.100", "a",
    "a" * 15, "...", "a-", "_tun",
])
def test_validate_interface_name_accepts_conservative_linux_names(value):
    assert validate_interface_name(value) is None


@pytest.mark.parametrize("value", [
    "", ".", "..", "a" * 16, "-eth0", "--help", "eth 0", "eth0\n", "eth0\0",
    "eth0/1", "eth0:1", "eth0;id", "+eth0", "@eth0", "ethö",
])
def test_validate_interface_name_rejects_invalid_names(value):
    with pytest.raises(ValidationError):
        validate_interface_name(value)


@pytest.mark.parametrize("value", [None, 0, b"eth0"])
def test_validate_interface_name_rejects_wrong_types(value):
    with pytest.raises(ValidationError, match="interface name must be a string"):
        validate_interface_name(value)


@pytest.mark.parametrize("arguments", [
    ["hostname"], ["ping", "-n", "-c", "3", "127.0.0.1"], ["echo", ""],
    ["printf", "two words"],
])
def test_validate_command_arguments_accepts_string_lists_with_command_name(arguments):
    original = list(arguments)

    assert validate_command_arguments(arguments) is None
    assert arguments == original


@pytest.mark.parametrize("arguments", [
    "hostname", ("hostname",), [], [1], [""], None, ["ping", None], ["ping", b"-c"],
])
def test_validate_command_arguments_rejects_invalid_lists(arguments):
    with pytest.raises(ValidationError, match="arguments must be a non-empty list"):
        validate_command_arguments(arguments)


@pytest.mark.parametrize("arguments", [["ping\0"], ["ping", "127.0.0.1\0"], ["ping", "\0"]])
def test_validate_command_arguments_rejects_nul_characters(arguments):
    with pytest.raises(ValidationError, match="arguments must not contain NUL"):
        validate_command_arguments(arguments)


@pytest.mark.parametrize("timeout", [10, 0.5, 3, 1e-9, 60, 60.0])
def test_validate_timeout_accepts_finite_positive_numbers(timeout):
    assert validate_timeout(timeout) is None


@pytest.mark.parametrize("timeout", [
    None, 0, 0.0, -1, -0.5, float("inf"), float("-inf"), float("nan"), "10", True, False,
])
def test_validate_timeout_rejects_invalid_values(timeout):
    with pytest.raises(ValidationError, match="timeout must be"):
        validate_timeout(timeout)


def test_validate_timeout_rejects_huge_integer_without_overflow_error():
    with pytest.raises(ValidationError, match="timeout must be"):
        validate_timeout(10 ** 400)


@pytest.mark.parametrize("value", [
    "0x7f", "0X7F.1", "0x7f.0.0.1", "0x7f000001", "1.0x2.3.4", "0x7f.1.", "017.0.0.1",
])
def test_validate_host_rejects_alternative_numeric_ipv4_forms(value):
    with pytest.raises(ValidationError, match="Invalid host"):
        validate_host(value)


@pytest.mark.parametrize("timeout", [60.000001, 61, 1e7, 10 ** 8, 1e300, 10 ** 400])
def test_validate_timeout_rejects_values_above_maximum(timeout):
    with pytest.raises(ValidationError, match="timeout must be"):
        validate_timeout(timeout)


@pytest.mark.parametrize("validator, value, expected", [
    (validate_host, "-f", "Invalid host: '-f' looks like a command option"),
    (validate_host, "999.1.2.3", "Invalid host: '999.1.2.3'"),
    (validate_ipv4_address, "-f", "Invalid IPv4 address: '-f'"),
    (validate_interface_name, "-f", "Invalid interface name: '-f'"),
    (validate_host, "a" * 64, "Invalid host: '" + "a" * 64 + "'"),
])
def test_validation_error_messages_keep_short_values_complete(validator, value, expected):
    with pytest.raises(ValidationError) as caught:
        validator(value)

    assert str(caught.value) == expected


@pytest.mark.parametrize("validator, value, expected", [
    (validate_host, "a" * 65, "Invalid host: '" + "a" * 64 + "'..."),
    (validate_host, "-" + "a" * 999, "Invalid host: '-" + "a" * 63 + "'... looks like a command option"),
    (validate_ipv4_address, "1" * 1000, "Invalid IPv4 address: '" + "1" * 64 + "'..."),
    (validate_interface_name, "e" * 1000, "Invalid interface name: '" + "e" * 64 + "'..."),
])
def test_validation_error_messages_truncate_long_values(validator, value, expected):
    with pytest.raises(ValidationError) as caught:
        validator(value)

    assert str(caught.value) == expected


@pytest.mark.parametrize("validator, prefix", [
    (validate_host, "Invalid host: "),
    (validate_ipv4_address, "Invalid IPv4 address: "),
    (validate_interface_name, "Invalid interface name: "),
])
def test_validation_error_messages_escape_control_characters(validator, prefix):
    value = "\x1b[31m\r\n\0\x7f" + "x" * 100

    with pytest.raises(ValidationError) as caught:
        validator(value)

    message = str(caught.value)
    assert message == prefix + "'\\x1b[31m\\r\\n\\x00\\x7f" + "x" * 55 + "'..."
    for character in ("\x1b", "\r", "\n", "\0", "\x7f"):
        assert character not in message


class ReprForbiddenString(str):
    def __repr__(self):
        raise AssertionError("The whole input must not be converted with repr()")


def test_validation_error_message_does_not_repr_the_whole_input():
    with pytest.raises(ValidationError) as caught:
        validate_host(ReprForbiddenString("a" * 100000 + ";"))

    assert str(caught.value) == "Invalid host: '" + "a" * 64 + "'..."
