"""Loopback security helpers: DPAPI, session checks, native pickers."""
from __future__ import annotations

import ctypes
import hmac
import os
import secrets
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class SecretError(Exception):
    pass


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is only available on Windows")
    in_blob, _keep = _blob(data)
    out_blob = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretError("DPAPI protect failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(blob: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("DPAPI is only available on Windows")
    in_blob, _keep = _blob(blob)
    out_blob = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretError("DPAPI unprotect failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def atomic_private_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Session:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)

    def matches(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(self.token, candidate)

    def cookie_header(self) -> str:
        return f"fd_session={self.token}; HttpOnly; SameSite=Strict; Path=/"


def host_allowed(host: str | None, port: int) -> bool:
    if not host:
        return False
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    return host.lower() in allowed


def origin_allowed(origin: str | None, port: int) -> bool:
    if not origin:
        return False
    return origin.lower() in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def cloud_sync_marker(path: str | Path) -> str | None:
    text = str(path).lower().replace("\\", "/")
    for marker in ("onedrive", "dropbox", "google drive", "googledrive", "icloud drive"):
        if marker in text:
            return marker
    return None


def pick_path(mode: str, *, timeout: int = 120) -> str | None:
    """Open a native dialog in the interactive user session. Paths are returned
    only to the local server, never logged.

    A dialog spawned by the server process is not owned by any window, so
    Windows is free to put it behind the browser — which looks exactly like a
    dead button, because the request is waiting on it. The owner form below is
    topmost, which brings the dialog to the front. If it still does not appear,
    callers must say so: the caller can type the path instead, and a silent
    no-op is the one outcome that must never happen.
    """
    if os.name != "nt":
        return None
    owner = (
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.TopMost=$true;$owner.ShowInTaskbar=$false;"
        "$owner.FormBorderStyle='None';$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.StartPosition='Manual';$owner.Location=New-Object System.Drawing.Point(-2000,-2000);"
        "$owner.Show();$owner.Activate();"
    )
    if mode == "folder":
        picker = (
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='Select the Flight Desk folder';"
            "if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($d.SelectedPath)}"
        )
    else:
        filt = (
            "Flight logs and archives (*.zip;*.txt)|*.zip;*.txt|"
            "ZIP archives (*.zip)|*.zip|DJI logs (*.txt)|*.txt|All files (*.*)|*.*"
        )
        picker = (
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            "$d.Title='Select an AirData export, handoff ZIP or flight log';"
            f"$d.Filter='{filt}';"
            "if($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
            "{[Console]::Out.Write($d.FileName)}"
        )
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;" + owner + picker +
        ";$owner.Close()"
    )
    kwargs = {"capture_output": True, "timeout": timeout}
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script], **kwargs
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    except ValueError:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or b"").decode("utf-8", "replace").strip()
    return value or None
