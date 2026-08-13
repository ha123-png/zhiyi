from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
from typing import Protocol
from uuid import uuid4


SECRET_REF_PREFIX = "wincred:"
TARGET_PREFIX = "DocumentPipeline/model-api/"
_SECRET_REF_PATTERN = re.compile(
    r"^wincred:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)


class SecretStoreError(RuntimeError):
    """A privacy-safe model secret storage failure."""


class SecretNotFoundError(SecretStoreError):
    """The requested model secret no longer exists for this Windows user."""


class CredentialBackend(Protocol):
    def write(self, target: str, secret: bytes) -> None: ...

    def read(self, target: str) -> bytes: ...

    def delete(self, target: str) -> None: ...


class ModelSecretStore:
    def __init__(self, backend: CredentialBackend) -> None:
        self._backend = backend

    def put(self, secret: str) -> str:
        if not secret:
            raise SecretStoreError("不能保存空的 AI 服务密钥。")
        secret_id = str(uuid4())
        target = f"{TARGET_PREFIX}{secret_id}"
        try:
            self._backend.write(target, secret.encode("utf-8"))
        except SecretStoreError:
            raise
        except Exception as error:
            raise SecretStoreError("无法把 AI 服务密钥保存到 Windows 凭据管理器。") from error
        return f"{SECRET_REF_PREFIX}{secret_id}"

    def get(self, secret_ref: str) -> str:
        target = _target_for_ref(secret_ref)
        try:
            value = self._backend.read(target)
        except SecretNotFoundError:
            raise
        except SecretStoreError:
            raise
        except Exception as error:
            raise SecretStoreError("无法从 Windows 凭据管理器读取 AI 服务密钥。") from error
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SecretStoreError("Windows 凭据管理器中的 AI 服务密钥已损坏。") from error

    def delete(self, secret_ref: str) -> None:
        target = _target_for_ref(secret_ref)
        try:
            self._backend.delete(target)
        except SecretNotFoundError:
            return
        except SecretStoreError:
            raise
        except Exception as error:
            raise SecretStoreError("无法从 Windows 凭据管理器删除 AI 服务密钥。") from error


def _target_for_ref(secret_ref: str) -> str:
    match = _SECRET_REF_PATTERN.fullmatch(secret_ref)
    if match is None:
        raise SecretStoreError("AI 服务密钥引用格式无效。")
    return f"{TARGET_PREFIX}{match.group(1)}"


if os.name == "nt":
    LPBYTE = ctypes.POINTER(wintypes.BYTE)

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", LPBYTE),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    PCREDENTIALW = ctypes.POINTER(CREDENTIALW)


class WindowsCredentialBackend:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretStoreError("系统密钥库只在 Windows 正式运行环境可用。")
        self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._cred_write = self._advapi32.CredWriteW
        self._cred_write.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self._cred_write.restype = wintypes.BOOL
        self._cred_read = self._advapi32.CredReadW
        self._cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(PCREDENTIALW),
        ]
        self._cred_read.restype = wintypes.BOOL
        self._cred_delete = self._advapi32.CredDeleteW
        self._cred_delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._cred_delete.restype = wintypes.BOOL
        self._cred_free = self._advapi32.CredFree
        self._cred_free.argtypes = [ctypes.c_void_p]
        self._cred_free.restype = None

    def write(self, target: str, secret: bytes) -> None:
        secret_buffer = ctypes.create_string_buffer(secret)
        credential = CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(secret)
        credential.CredentialBlob = ctypes.cast(secret_buffer, LPBYTE)
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "DocumentPipeline"
        try:
            if not self._cred_write(ctypes.byref(credential), 0):
                raise SecretStoreError("Windows 凭据管理器拒绝保存 AI 服务密钥。")
        finally:
            ctypes.memset(secret_buffer, 0, len(secret_buffer))

    def read(self, target: str) -> bytes:
        credential_pointer = PCREDENTIALW()
        if not self._cred_read(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            if ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                raise SecretNotFoundError("任务引用的 AI 服务密钥不存在。")
            raise SecretStoreError("Windows 凭据管理器拒绝读取 AI 服务密钥。")
        try:
            credential = credential_pointer.contents
            value = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            if credential.CredentialBlob and credential.CredentialBlobSize:
                ctypes.memset(
                    credential.CredentialBlob,
                    0,
                    credential.CredentialBlobSize,
                )
            return value
        finally:
            self._cred_free(credential_pointer)

    def delete(self, target: str) -> None:
        if self._cred_delete(target, self.CRED_TYPE_GENERIC, 0):
            return
        if ctypes.get_last_error() == self.ERROR_NOT_FOUND:
            raise SecretNotFoundError("任务引用的 AI 服务密钥不存在。")
        raise SecretStoreError("Windows 凭据管理器拒绝删除 AI 服务密钥。")


def create_model_secret_store() -> ModelSecretStore:
    return ModelSecretStore(WindowsCredentialBackend())
