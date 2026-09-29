"""Async SSH client for network devices.

Uses a single persistent shell per device so that per-session settings
(e.g. Cisco 'terminal length 0') stay in effect across commands. Output is
collected until the platform-specific prompt pattern appears at the end of
the buffer.
"""
from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path

import asyncssh

from .config import APP_CONFIG_DIR, DeviceConfig
from .platforms import Platform


class DeviceError(Exception):
    """Raised when a connection fails or a command times out."""


class DeviceSession:
    """Manages a persistent SSH shell to a single network device."""

    def __init__(self, device: DeviceConfig):
        self.device = device
        self.platform: Platform = device.platform_def
        self._conn: asyncssh.SSHClientConnection | None = None
        self._channel: asyncssh.SSHClientProcess | None = None
        self._lock = asyncio.Lock()

    # -- host key handling ---------------------------------------------------

    def _app_known_hosts_path(self) -> Path:
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return APP_CONFIG_DIR / "known_hosts"

    def _host_in_known_hosts(self, kh_path: Path) -> bool:
        """Return True if the device host already has a trusted key in the file."""
        if not kh_path.is_file() or kh_path.stat().st_size == 0:
            return False
        port = self.device.port if self.device.port != 22 else None
        try:
            trusted, *_ = asyncssh.match_known_hosts(
                str(kh_path), self.device.host, self.device.host, port
            )
        except Exception:
            return False
        return len(trusted) > 0

    def _save_host_key(self, kh_path: Path) -> None:
        """Append the server host key to the known_hosts file (TOFU)."""
        if self._conn is None:
            return
        key = self._conn.get_server_host_key()
        if key is None:
            return
        host = self.device.host
        port = self.device.port
        host_pattern = f"[{host}]:{port}" if port != 22 else host
        line = (
            f"{host_pattern} {key.algorithm.decode()} "
            f"{base64.b64encode(key.public_data).decode()}"
        )
        try:
            with open(kh_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _host_key_options(self) -> tuple[str | None, bool]:
        """Return (known_hosts arg, should_save_new_key) for the device mode.

        - strict:   verify against the default ~/.ssh/known_hosts
        - no-check: no host key verification
        - auto:     app-managed known_hosts file (TOFU: accept & save new keys)
        """
        mode = self.device.known_hosts
        if mode == "strict":
            return "", False
        if mode == "no-check":
            return None, False
        kh_path = self._app_known_hosts_path()
        if self._host_in_known_hosts(kh_path):
            return str(kh_path), False
        return None, True

    # -- connection setup ----------------------------------------------------

    async def _connect(self) -> None:
        known_hosts, should_save = self._host_key_options()
        kh_path = self._app_known_hosts_path() if should_save else None

        kwargs: dict = dict(
            host=self.device.host,
            port=self.device.port,
            username=self.device.username,
            known_hosts=known_hosts,
        )
        if self.device.ssh_key:
            key = Path(self.device.ssh_key).expanduser()
            if not key.exists():
                raise DeviceError(f"SSH key not found: {key}")
            kwargs["client_keys"] = [str(key)]
        else:
            kwargs["password"] = self.device.password

        try:
            self._conn = await asyncio.wait_for(asyncssh.connect(**kwargs), timeout=20)
        except asyncssh.HostKeyNotVerifiable as e:
            raise DeviceError(
                f"Host key verification failed for {self.device.host}: {e}"
            ) from e
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
            raise DeviceError(
                f"Cannot connect to {self.device.host}:{self.device.port}: {e}"
            ) from e

        if should_save and kh_path is not None:
            self._save_host_key(kh_path)

    async def _ensure_shell(self) -> None:
        """Open the connection + interactive shell and run platform setup."""
        if self._channel is not None and not self._channel.is_closed():
            return
        if self._conn is None or self._conn.is_closed():
            await self._connect()
        self._channel = await self._conn.create_process(
            term_type="vt100", echo=False, errors="replace"
        )
        # Discard the login banner up to the first prompt.
        await self._read_until_prompt(timeout=20)
        for setup in self.platform.setup_commands:
            await self._send_raw(setup, timeout=20)

    # -- prompt handling -------------------------------------------------------

    @property
    def _prompt_re(self) -> re.Pattern:
        return re.compile(self.platform.prompt_pattern)

    async def _read_until_prompt(self, timeout: float) -> str:
        """Read from the channel until the prompt appears at the end of the buffer."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        buffer = ""
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise DeviceError(
                    f"Timed out after {timeout:.0f}s waiting for prompt on "
                    f"{self.device.name}. Last output:\n{buffer[-800:]}"
                )
            try:
                chunk = await asyncio.wait_for(
                    self._channel.read(), timeout=min(remaining, 10.0)
                )
            except asyncio.TimeoutError:
                continue
            if chunk is None:
                raise DeviceError("SSH channel closed unexpectedly")
            buffer += chunk
            stripped = buffer.rstrip()
            m = self._prompt_re.search(stripped)
            if m and m.end() == len(stripped):
                return stripped[: m.start()]

    async def _send_raw(self, command: str, timeout: float) -> str:
        """Send one command line and return its output (echo and prompt removed)."""
        self._channel.send(command + "\n")
        output = await self._read_until_prompt(timeout=timeout)
        lines = output.splitlines()
        # Remove the echoed command (first line) when present.
        if lines and lines[0].strip() == command.strip():
            lines = lines[1:]
        return "\n".join(lines).strip()

    # -- public API --------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the SSH connection and shell (idempotent)."""
        async with self._lock:
            await self._ensure_shell()

    async def run(self, command: str, timeout: float | None = None) -> str:
        """Run a command on the device and return its output."""
        async with self._lock:
            await self._ensure_shell()
            timeout = timeout or self.device.timeout
            try:
                return await self._send_raw(command, timeout=timeout)
            except (asyncssh.Error, OSError, ConnectionError):
                # Drop the session so the next call reconnects cleanly.
                await self.close()
                raise

    async def close(self) -> None:
        """Close the shell and connection."""
        self._channel = None
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
