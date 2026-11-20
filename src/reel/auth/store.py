"""Where OAuth tokens are kept (T-701; NFR-5, R-7).

- ``WindowsCredentialStore``: Windows Credential Manager (generic credentials, encrypted by
  Windows for the signed-in user), through ``advapi32`` with ``ctypes`` — no extra package.
- ``FileSecretStore``: a JSON file for development only; every use logs a warning.
- ``MemorySecretStore``: tests.

Static secrets (Slack token, API tokens, the path of the Google client file) stay in the
environment or ``.env`` (``reel.config.secrets``). Values are never logged.
"""

import ctypes
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reel.config.loader import LoadedConfig

log = logging.getLogger("reel.auth")

MAX_VALUE_BYTES = 2560  # CRED_MAX_CREDENTIAL_BLOB_SIZE


class SecretStoreError(RuntimeError):
    """The store cannot be used (wrong platform, access denied, value too large)."""


class SecretStore(ABC):
    name: str

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove the value; False when there was none."""


class MemorySecretStore(SecretStore):
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FileSecretStore(SecretStore):
    """Plain JSON on disk — for development on a machine without a credential store."""

    name = "file"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _warn(self) -> None:
        log.warning("OAuth tokens are kept in a plain file (%s): development only", self.path.name)

    def _read(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SecretStoreError(f"cannot read {self.path.name}: {type(exc).__name__}") from None
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def get(self, key: str) -> str | None:
        self._warn()
        return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        self._warn()
        data = self._read()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".part")
        partial.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(partial, self.path)

    def delete(self, key: str) -> bool:
        data = self._read()
        if key not in data:
            return False
        del data[key]
        partial = self.path.with_name(self.path.name + ".part")
        partial.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(partial, self.path)
        return True


if sys.platform == "win32":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _api = ctypes.WinDLL("advapi32", use_last_error=True)
    _api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _api.CredWriteW.restype = wintypes.BOOL
    _api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIAL)),
    ]
    _api.CredReadW.restype = wintypes.BOOL
    _api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _api.CredDeleteW.restype = wintypes.BOOL
    _api.CredFree.argtypes = [ctypes.c_void_p]
    _api.CredFree.restype = None

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # this user on this computer; survives a restart
ERROR_NOT_FOUND = 1168


class WindowsCredentialStore(SecretStore):
    """Generic credentials named ``<namespace>/<key>`` in Windows Credential Manager."""

    name = "windows"

    def __init__(self, namespace: str = "Reel") -> None:
        if sys.platform != "win32":
            raise SecretStoreError("Windows Credential Manager is only available on Windows")
        self.namespace = namespace

    def _target(self, key: str) -> str:
        return f"{self.namespace}/{key}"

    def get(self, key: str) -> str | None:
        pointer = ctypes.POINTER(_CREDENTIAL)()
        if not _api.CredReadW(self._target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise SecretStoreError(f"Credential Manager read failed (Windows error {error})")
        try:
            credential = pointer.contents
            blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return blob.decode("utf-8")
        finally:
            _api.CredFree(pointer)

    def set(self, key: str, value: str) -> None:
        data = value.encode("utf-8")
        if len(data) > MAX_VALUE_BYTES:
            raise SecretStoreError(f"value too large for Credential Manager ({len(data)} bytes)")
        buffer = (ctypes.c_ubyte * max(len(data), 1)).from_buffer_copy(data or b"\0")
        credential = _CREDENTIAL(
            Type=CRED_TYPE_GENERIC,
            TargetName=self._target(key),
            Comment="Reel (lesson video pipeline)",
            CredentialBlobSize=len(data),
            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            Persist=CRED_PERSIST_LOCAL_MACHINE,
            UserName="reel",
        )
        if not _api.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise SecretStoreError(f"Credential Manager write failed (Windows error {error})")

    def delete(self, key: str) -> bool:
        if _api.CredDeleteW(self._target(key), CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return False
        raise SecretStoreError(f"Credential Manager delete failed (Windows error {error})")


def make_secret_store(cfg: LoadedConfig) -> SecretStore:
    settings = cfg.settings.auth
    if settings.store == "file":
        return FileSecretStore(cfg.resolve(settings.dev_file))
    return WindowsCredentialStore(settings.store_namespace)
