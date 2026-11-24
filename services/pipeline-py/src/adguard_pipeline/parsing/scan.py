"""Antivirus scanning for uploaded assets (FR-1 / NFR security).

Engines:
- ClamAV daemon (primary when configured): supports a loopback TCP endpoint
  (ADGUARD_CLAMD_ADDR=host:port) or a Unix socket (ADGUARD_CLAMD_SOCKET=/path). Both use the
  clamd INSTREAM protocol; the Unix socket is the default recommendation on the ECS.
- Built-in EICAR signature fallback for local dev / CI when no daemon is configured.

If a daemon is configured but unreachable the verdict is `unknown` (never a silent `clean`), so
the gateway can quarantine (ADGUARD_SCAN_REQUIRED=1) or continue with an explicit degraded mark.
"""

from __future__ import annotations

import os
import socket
import struct

from .types import ScanResult

EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _instream(conn: socket.socket, data: bytes, timeout: float) -> ScanResult:
    try:
        conn.settimeout(timeout)
        conn.sendall(b"zINSTREAM\0")
        for offset in range(0, len(data), 8192):
            chunk = data[offset : offset + 8192]
            conn.sendall(struct.pack(">I", len(chunk)) + chunk)
        conn.sendall(struct.pack(">I", 0))
        response = b""
        while True:
            part = conn.recv(4096)
            if not part:
                break
            response += part
            if response.endswith(b"\0") or b"FOUND" in response or b"OK" in response:
                break
    except OSError as exc:
        return ScanResult(status="unknown", engine="clamav", warnings=[f"clamav unreachable: {exc}"])
    text = response.decode("utf-8", "replace").strip("\0").strip()
    if text.endswith("FOUND") or ("FOUND" in text and "OK" not in text):
        signature = text.split(":", 1)[-1].replace("FOUND", "").strip()
        return ScanResult(status="infected", engine="clamav", signature=signature or "unknown")
    if text.endswith("OK") or ": OK" in text:
        return ScanResult(status="clean", engine="clamav")
    return ScanResult(status="unknown", engine="clamav", warnings=[f"unexpected clamd reply: {text[:120]}"])


def _clamav_tcp(data: bytes, addr: str, timeout: float) -> ScanResult:
    host, _, port_s = addr.rpartition(":")
    if not host or not port_s.isdigit():
        return ScanResult(status="unknown", engine="clamav", warnings=[f"invalid ADGUARD_CLAMD_ADDR={addr!r}"])
    try:
        conn = socket.create_connection((host, int(port_s)), timeout=timeout)
    except OSError as exc:
        return ScanResult(status="unknown", engine="clamav", warnings=[f"clamav unreachable: {exc}"])
    with conn:
        return _instream(conn, data, timeout)


def _clamav_unix(data: bytes, path: str, timeout: float) -> ScanResult:
    if not hasattr(socket, "AF_UNIX"):
        return ScanResult(status="unknown", engine="clamav", warnings=["AF_UNIX not supported on this platform"])
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.settimeout(timeout)
        conn.connect(path)
    except OSError as exc:
        conn.close()
        return ScanResult(status="unknown", engine="clamav", warnings=[f"clamav unreachable: {exc}"])
    with conn:
        return _instream(conn, data, timeout)


def _eicar_in(data: bytes) -> bool:
    return EICAR.encode() in data or b"eicar-standard-antivirus-test-file" in data.lower()


def scan_bytes(data: bytes, filename: str = "") -> ScanResult:
    """Return the antivirus verdict for `data`. Never raises."""
    timeout = float(os.environ.get("ADGUARD_CLAMD_TIMEOUT", "15"))
    addr = os.environ.get("ADGUARD_CLAMD_ADDR", "").strip()
    sock = os.environ.get("ADGUARD_CLAMD_SOCKET", "").strip()
    if addr or sock:
        result = _clamav_tcp(data, addr, timeout) if addr else _clamav_unix(data, sock, timeout)
        # Defense in depth: ClamAV's EICAR signature is anchored near the file start, so a test
        # file with appended metadata can slip through. The EICAR substring check is a cheap
        # belt-and-braces guard that never produces false positives in production content.
        if result.status != "infected" and _eicar_in(data):
            return ScanResult(status="infected", engine="eicar", signature="EICAR-Test-File")
        return result
    if _eicar_in(data):
        return ScanResult(status="infected", engine="eicar", signature="EICAR-Test-File")
    return ScanResult(
        status="clean",
        engine="eicar",
        warnings=["clamav not configured (ADGUARD_CLAMD_ADDR/ADGUARD_CLAMD_SOCKET empty); only EICAR signature checked"],
    )


def scan_required() -> bool:
    return os.environ.get("ADGUARD_SCAN_REQUIRED", "0") in {"1", "true", "yes"}
