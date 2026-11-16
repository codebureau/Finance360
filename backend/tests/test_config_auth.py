"""Auth settings read from the environment (ADR-0010)."""

from pathlib import Path

import pytest

from finance360.config import Settings


def test_auth_defaults_are_secure_and_password_only() -> None:
    s = Settings.from_env({})
    assert s.auth_providers == ("password",)
    assert s.cookie_secure is True
    assert s.owner_password is None


@pytest.mark.parametrize("off", ["false", "False", "0", "no", "off"])
def test_cookie_secure_can_be_turned_off_only_explicitly(off: str) -> None:
    assert Settings.from_env({"FINANCE360_COOKIE_SECURE": off}).cookie_secure is False
    assert Settings.from_env({"FINANCE360_COOKIE_SECURE": "true"}).cookie_secure is True


def test_allowed_hosts_default_to_local_names_and_parse_a_comma_list() -> None:
    assert Settings.from_env({}).allowed_hosts == ("localhost", "127.0.0.1")
    assert Settings.from_env({"FINANCE360_ALLOWED_HOSTS": " "}).allowed_hosts == (
        "localhost",
        "127.0.0.1",
    )
    s = Settings.from_env(
        {"FINANCE360_ALLOWED_HOSTS": "192.0.2.10:8843, Finance.Tailnet.Example,192.0.2.10:8843"}
    )
    assert s.allowed_hosts == ("192.0.2.10:8843", "finance.tailnet.example")


@pytest.mark.parametrize("bad", ["https://a.example", "a.example/x", "a b", "*.example", "a:b"])
def test_a_malformed_allowed_host_fails_loudly(bad: str) -> None:
    with pytest.raises(ValueError, match="FINANCE360_ALLOWED_HOSTS"):
        Settings.from_env({"FINANCE360_ALLOWED_HOSTS": f"localhost,{bad}"})


def test_providers_are_a_comma_list() -> None:
    s = Settings.from_env({"FINANCE360_AUTH_PROVIDERS": "password, other"})
    assert s.auth_providers == ("password", "other")


def test_owner_password_from_env_and_file_preferring_the_file(tmp_path: Path) -> None:
    from_env = Settings.from_env({"FINANCE360_OWNER_PASSWORD": "synthetic-env"})
    assert from_env.owner_password == "synthetic-env"
    file = tmp_path / "pw"
    file.write_bytes(b"synthetic-file\r\n")
    both = {
        "FINANCE360_OWNER_PASSWORD": "synthetic-env",
        "FINANCE360_OWNER_PASSWORD_FILE": str(file),
    }
    assert Settings.from_env(both).owner_password == "synthetic-file"


def test_a_missing_password_file_is_a_loud_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        Settings.from_env({"FINANCE360_OWNER_PASSWORD_FILE": str(tmp_path / "absent")})


def test_the_seed_password_never_appears_in_repr() -> None:
    s = Settings.from_env({"FINANCE360_OWNER_PASSWORD": "synthetic-env-value"})
    assert "synthetic-env-value" not in repr(s)
