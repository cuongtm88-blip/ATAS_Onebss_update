"""OS-backed storage for OneBSS credentials (never config/log files)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SERVICE_PREFIX = "vn.cnttdvs.ats-onebss.login."


class CredentialStoreError(RuntimeError):
    pass


def _service(region_key: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "-_ ." else "-"
        for char in region_key.strip().lower()
    ).replace(" ", "-")
    return SERVICE_PREFIX + (cleaned or "default")


def _mac_keychain(region_key: str, account: str, value: str | None) -> str:
    command = ["/usr/bin/security"]
    if value is None:
        command += ["find-generic-password", "-s", _service(region_key), "-a", account, "-w"]
    else:
        command += ["add-generic-password", "-U", "-s", _service(region_key), "-a", account, "-w", value]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if value is None:
        return result.stdout.strip() if result.returncode == 0 else ""
    if result.returncode:
        raise CredentialStoreError("Không lưu được thông tin OneBSS vào Keychain macOS.")
    return ""


def _windows_path(region_key: str) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    slug = "".join(char if char.isalnum() or char in "-_" else "-" for char in region_key)
    return base / "ATS-OneBSS" / "credentials" / f"{slug or 'default'}.dat"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = DataBlob()
    crypt = ctypes.windll.crypt32
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(source), "ATS OneBSS", None, None, None, 1, ctypes.byref(output))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise CredentialStoreError("Windows không đọc/ghi được thông tin đăng nhập đã mã hóa.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def save_credentials(region_key: str, username: str, password: str) -> None:
    user = username.strip()
    if not user or not password:
        raise CredentialStoreError("Hãy nhập cả tên đăng nhập và mật khẩu OneBSS.")
    if sys.platform == "darwin":
        _mac_keychain(region_key, "onebss-username", user)
        _mac_keychain(region_key, "onebss-password", password)
    elif os.name == "nt":
        path = _windows_path(region_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _dpapi(json.dumps({"username": user, "password": password}).encode("utf-8"), True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(path)
    else:
        raise CredentialStoreError("Lưu thông tin OneBSS được hỗ trợ trên macOS và Windows.")


def load_credentials(region_key: str) -> tuple[str, str]:
    if sys.platform == "darwin":
        return (
            _mac_keychain(region_key, "onebss-username", None),
            _mac_keychain(region_key, "onebss-password", None),
        )
    if os.name == "nt":
        path = _windows_path(region_key)
        if not path.is_file():
            return "", ""
        try:
            values = json.loads(_dpapi(path.read_bytes(), False).decode("utf-8"))
            return str(values.get("username", "")), str(values.get("password", ""))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise CredentialStoreError("Không đọc được thông tin OneBSS đã mã hóa.") from error
    return "", ""


def save_ingest_api_token(region_key: str, token: str) -> None:
    value = token.strip()
    if not value:
        raise CredentialStoreError("Hãy nhập API Token.")
    if sys.platform == "darwin":
        _mac_keychain(region_key, "ingest-api-token", value)
    elif os.name == "nt":
        path = _windows_path(region_key).with_name(
            _windows_path(region_key).stem + "-ingest.dat"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(_dpapi(value.encode("utf-8"), True))
        temporary.replace(path)
    else:
        raise CredentialStoreError("Lưu API Token được hỗ trợ trên macOS và Windows.")


def load_ingest_api_token(region_key: str) -> str:
    if sys.platform == "darwin":
        return _mac_keychain(region_key, "ingest-api-token", None)
    if os.name == "nt":
        base = _windows_path(region_key)
        path = base.with_name(base.stem + "-ingest.dat")
        if not path.is_file():
            return ""
        try:
            return _dpapi(path.read_bytes(), False).decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise CredentialStoreError("Không đọc được API Token đã mã hóa.") from error
    return ""
