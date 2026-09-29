"""MCP server exposing read-only network device operations."""
from __future__ import annotations

import argparse
import re

from fastmcp import FastMCP

from . import __version__
from .config import DeviceConfig, load_devices
from .platforms import Platform, list_platforms
from .ssh_client import DeviceError, DeviceSession

MAX_OUTPUT_CHARS = 50_000
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:]*$")

mcp = FastMCP(
    "mcp-llmnetops",
    instructions=(
        "Read-only network device operations over SSH. "
        "Workflow: call list_devices to see configured devices, "
        "list_commands to see the commands allowed for a device's platform, "
        "then run_command to execute one. Commands are strictly whitelisted "
        "per platform; anything else is rejected."
    ),
)

_sessions: dict[str, DeviceSession] = {}
_config_override: str | None = None


# -- helpers -----------------------------------------------------------------


def _devices() -> dict[str, DeviceConfig]:
    return load_devices(_config_override)


def _get_device(name: str) -> DeviceConfig:
    devices = _devices()
    if name not in devices:
        available = ", ".join(sorted(devices)) or "(none configured)"
        raise ValueError(f"Unknown device '{name}'. Configured devices: {available}")
    return devices[name]


def _session_for(device: DeviceConfig) -> DeviceSession:
    if device.name not in _sessions:
        _sessions[device.name] = DeviceSession(device)
    return _sessions[device.name]


def _match_command(platform: Platform, command: str):
    norm = " ".join(command.strip().lower().split())
    for c in platform.commands:
        if c.name.lower() == norm:
            return c
        base = c.command.split("{")[0].strip().lower()
        if base == norm:
            return c
    return None


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n\n... [truncated {len(text) - MAX_OUTPUT_CHARS} characters]"


# -- tools ---------------------------------------------------------------------


@mcp.tool()
async def list_devices() -> str:
    """List all configured network devices with host, port, and platform."""
    devices = _devices()
    if not devices:
        return (
            "No devices configured. Create a devices.yaml (see devices.example.yaml) "
            "and point to it with --config or the MCP_LLMNETOPS_CONFIG environment variable."
        )
    lines = []
    for d in devices.values():
        lines.append(f"- {d.name}: {d.host}:{d.port} [{d.platform}] user={d.username}")
    return "\n".join(lines)


@mcp.tool()
async def list_platforms() -> str:
    """List all supported device platforms and their command counts."""
    lines = []
    for p in list_platforms():
        lines.append(f"- {p.key}: {p.description} ({len(p.commands)} commands)")
    return "\n".join(lines)


@mcp.tool()
async def list_commands(device: str) -> str:
    """List the commands available on a device (whitelisted for its platform).

    Args:
        device: Device name as returned by list_devices.
    """
    dev = _get_device(device)
    plat = dev.platform_def
    lines = [f"Device: {dev.name} ({dev.host}:{dev.port}) — platform: {plat.key}"]
    for c in plat.commands:
        suffix = " [requires target]" if c.requires_target else ""
        lines.append(f"- {c.name}{suffix}: {c.description}")
    return "\n".join(lines)


@mcp.tool()
async def run_command(device: str, command: str, target: str = "") -> str:
    """Run a whitelisted command on a network device and return its output.

    Args:
        device: Device name as returned by list_devices.
        command: Command to run, exactly as listed by list_commands
            (e.g. "show ip route", "/ip route print", "display version").
        target: Required for ping commands — the hostname or IP address to ping.
    """
    dev = _get_device(device)
    plat = dev.platform_def
    cmd = _match_command(plat, command)
    if cmd is None:
        available = ", ".join(c.name for c in plat.commands)
        raise ValueError(
            f"Command '{command}' is not allowed on {dev.name} ({plat.key}). "
            f"Available commands: {available}"
        )
    if cmd.requires_target:
        if not target:
            raise ValueError(f"Command '{cmd.name}' requires a 'target' (hostname or IP address).")
        if not TARGET_RE.match(target):
            raise ValueError(f"Invalid target '{target}'. Use a plain hostname or IP address.")
        actual = cmd.command.format(target=target)
    else:
        actual = cmd.command

    session = _session_for(dev)
    try:
        output = await session.run(actual)
    except DeviceError as e:
        raise ValueError(str(e)) from e
    return _truncate(output) if output else "(no output)"


@mcp.tool()
async def test_connection(device: str) -> str:
    """Test the SSH connection to a device (connects and opens a shell).

    Args:
        device: Device name as returned by list_devices.
    """
    dev = _get_device(device)
    session = _session_for(dev)
    try:
        await session.connect()
    except DeviceError as e:
        return f"FAILED: {e}"
    return f"OK: connected to {dev.name} ({dev.host}:{dev.port}) as {dev.username}"


# -- entry point -----------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mcp-llmnetops",
        description="MCP server for read-only network device operations",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to devices.yaml (default: $MCP_LLMNETOPS_CONFIG, ./devices.yaml, "
        "~/.config/mcp-llmnetops/devices.yaml)",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    global _config_override
    _config_override = args.config
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()