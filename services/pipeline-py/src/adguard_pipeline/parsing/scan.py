"""Antivirus scanning for uploaded assets (FR-1 / NFR security).

Policy:
- EICAR test signature is always checked (cheap, deterministic, testable).
- If ADGUARD_CLAMD_ADDR=host:port is configured, bytes are additionally streamed to a local
  ClamAV daemon (INSTREAM protocol). If the daemon is unreachable the verdict is `unknown`
  (never silently `clean`), so the caller can decide: route to manual/quarantine or continue
  with a degraded marker (ADGUARD_SCAN_REQUIRED=1 forces manual on unknown).
"""

from __future__ import annotations

import os
import socket
import struct

from .types import ScanResult

EICAR = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def _clamav_scan(data: bytes, addr: str, timeout: float = 10.0) -> ScanResult:
    host, _, port_s = addr.rpartition(":")
    if not host or not port_s.isdigit():
        return ScanResult(status="unknown", engine="clamav", warnings=[f"invalid ADGUARD_CLAMD_ADDR={addr!r}"])
    try:
        with socket.create_connection((host, int(port_s)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\0")
            for offset in range(0, len(data), 8192):
                chunk = data[offset : offset + 8192]
                sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            sock.sendall(struct.pack(">I", 0))
            response = b""
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                response += part
                if b"\0" in response or b"FOUND" in response or b"OK" in response:
                    # clamd replies with a single NUL-terminated line
                    if response.endswith(b"\0") or b"FOUND" in response or b"OK" in response:
                        break
    except OSError as exc:
        return ScanResult(status="unknown", engine="clamav", warnings=[f"clamav unreachable: {exc}"])
    text = response.decode("utf-8", "replace").strip("\0").strip()
    if text.endswith("FOUND"):
        signature = text.split(":", 1)[-1].replace("FOUND", "").strip()
        return ScanResult(status="infected", engine="clamav", signature=signature or "unknown")
    if text.endswith("OK") or ": OK" in text:
        return ScanResult(status="clean", engine="clamav")
    if "FOUND" in text:
        return ScanResult(status="infected", engine="clamav", signature=text)
    return ScanResult(status="unknown", engine="clamav", warnings=[f"unexpected clamd reply: {text[:120]}"])


def scan_bytes(data: bytes, filename: str = "") -> ScanResult:
    """Return the antivirus verdict for `data`. Never raises."""
    lowered = data.lower()
    if EICAR.encode() in data or b"eicar-standard-antivirus-test-file" in lowered:
        return ScanResult(status="infected", engine="eicar", signature="EICAR-Test-File")
    addr = os.environ.get("ADGUARD_CLAMD_ADDR", "").strip()
    if not addr:
        return ScanResult(
            status="clean",
            engine="eicar",
            warnings=["clamav not configured (ADGUARD_CLAMD_ADDR empty); only EICAR signature checked"],
        )
    return _clamav_scan(data, addr)


def scan_required() -> bool:
    return os.environ.get("ADGUARD_SCAN_REQUIRED", "0") in {"1", "true", "yes"}
