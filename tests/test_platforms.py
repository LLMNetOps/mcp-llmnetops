"""Tests for platform definitions, config loading, and command matching."""
import os
import textwrap
from pathlib import Path

import pytest

from mcp_llmnetops.config import load_devices
from mcp_llmnetops.platforms import PLATFORMS, get_platform, list_platforms
from mcp_llmnetops.server import _match_command, _truncate, TARGET_RE


# -- platforms -----------------------------------------------------------------


def test_all_expected_platforms_registered():
    expected = {
        "mikrotik-ros6",
        "mikrotik-ros7",
        "ruckus-fastiron",
        "cisco-ios",
        "cisco-iosxe",
        "cisco-iosxr",
        "cisco-nxos",
        "juniper-junos",
        "aruba-aos-cx",
        "huawei-vrp",
    }
    assert set(PLATFORMS) == expected


def test_every_platform_has_commands_and_prompt():
    for p in list_platforms():
        assert p.commands, f"{p.key} has no commands"
        assert p.prompt_pattern, f"{p.key} has no prompt pattern"
        for c in p.commands:
            assert c.name and c.command and c.description
            if c.requires_target:
                assert "{target}" in c.command
            else:
                assert "{target}" not in c.command


def test_get_platform():
    assert get_platform("cisco-ios") is PLATFORMS["cisco-ios"]
    assert get_platform("nope") is None


# -- command matching ------------------------------------------------------------


def test_match_command_exact():
    plat = PLATFORMS["cisco-ios"]
    assert _match_command(plat, "show ip route").name == "show ip route"


def test_match_command_case_and_whitespace_insensitive():
    plat = PLATFORMS["cisco-ios"]
    assert _match_command(plat, "  SHOW   IP  ROUTE ").name == "show ip route"


def test_match_command_ping_template():
    plat = PLATFORMS["mikrotik-ros7"]
    cmd = _match_command(plat, "/ping")
    assert cmd is not None and cmd.requires_target


def test_match_command_unknown_returns_none():
    plat = PLATFORMS["cisco-ios"]
    assert _match_command(plat, "configure terminal") is None
    assert _match_command(plat, "show ip route vrf all") is None


def test_target_regex():
    assert TARGET_RE.match("8.8.8.8")
    assert TARGET_RE.match("core-rtr-01.local")
    assert TARGET_RE.match("2001:db8::1")
    assert not TARGET_RE.match("8.8.8.8; rm -rf /")
    assert not TARGET_RE.match("$(whoami)")
    assert not TARGET_RE.match("")


# -- config loading ---------------------------------------------------------------


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "devices.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_load_devices_basic(tmp_path):
    p = _write(
        tmp_path,
        """
        devices:
          - name: r1
            host: 10.0.0.1
            platform: cisco-ios
            username: admin
            password: secret
        """,
    )
    devices = load_devices(p)
    assert "r1" in devices
    assert devices["r1"].host == "10.0.0.1"
    assert devices["r1"].platform == "cisco-ios"
    assert devices["r1"].port == 22
    assert devices["r1"].known_hosts == "auto"


def test_load_devices_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DEV_PASSWORD", "s3cret")
    p = _write(
        tmp_path,
        """
        devices:
          - name: r1
            host: 10.0.0.1
            platform: mikrotik-ros6
            username: admin
            password: ${TEST_DEV_PASSWORD}
        """,
    )
    devices = load_devices(p)
    assert devices["r1"].password == "s3cret"


def test_load_devices_unknown_platform(tmp_path):
    p = _write(
        tmp_path,
        """
        devices:
          - name: r1
            host: 10.0.0.1
            platform: fake-os
            username: admin
            password: x
        """,
    )
    with pytest.raises(ValueError, match="unknown platform"):
        load_devices(p)


def test_load_devices_missing_credentials(tmp_path):
    p = _write(
        tmp_path,
        """
        devices:
          - name: r1
            host: 10.0.0.1
            platform: cisco-ios
            username: admin
        """,
    )
    with pytest.raises(ValueError, match="password.*ssh_key"):
        load_devices(p)


def test_load_devices_duplicate_name(tmp_path):
    p = _write(
        tmp_path,
        """
        devices:
          - name: r1
            host: 10.0.0.1
            platform: cisco-ios
            username: a
            password: x
          - name: r1
            host: 10.0.0.2
            platform: cisco-ios
            username: a
            password: x
        """,
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_devices(p)


def test_load_devices_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_devices(Path("/nonexistent/devices.yaml"))


# -- output truncation ---------------------------------------------------------------


def test_truncate_short_output_unchanged():
    assert _truncate("hello") == "hello"


def test_truncate_long_output():
    text = "x" * 60_000
    out = _truncate(text)
    assert len(out) < 60_000
    assert "truncated" in out