"""
kaoruko/security/secrets_manager.py

Encrypted local secrets storage for API keys and sensitive config.
Uses Fernet (AES-128-CBC + HMAC-SHA256) with machine-tied key derivation.

Security model:
- Master key derived from machine UUID + user salt (PBKDF2, 100k iterations)
- All secrets stored as Fernet-encrypted JSON in data/secrets.enc
- API keys are never written to logs or config files
- Memory is cleared after use where possible
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
import base64

from kaoruko.infrastructure.logging.logger import get_logger

log = get_logger("security.secrets_manager")

_SECRETS_FILE = "data/secrets.enc"
_SALT_FILE    = "data/.salt"


class SecretsError(Exception):
    """Raised on encryption/decryption failures."""


class SecretsManager:
    """
    Encrypted local secrets storage.

    Usage:
        mgr = SecretsManager(project_root)
        mgr.store("anthropic_api_key", "sk-ant-...")
        key = mgr.get("anthropic_api_key")
        keys = mgr.list_keys()  # Returns names only, never values
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._secrets_path = project_root / _SECRETS_FILE
        self._salt_path = project_root / _SALT_FILE
        self._fernet: Optional[Fernet] = None
        self._cache: dict[str, str] = {}
        self._initialize_cipher()

    # ── Public API ────────────────────────────────────────────────────────────

    def store(self, name: str, value: str) -> None:
        """Encrypt and store a secret."""
        if not name or not isinstance(name, str):
            raise SecretsError("Secret name must be a non-empty string")
        if not value or not isinstance(value, str):
            raise SecretsError("Secret value must be a non-empty string")

        secrets = self._load_all()
        secrets[name] = value
        self._save_all(secrets)
        self._cache[name] = value
        log.info("secret_stored", name=name)  # Value deliberately NOT logged

    def get(self, name: str) -> Optional[str]:
        """Retrieve a secret by name."""
        if name in self._cache:
            return self._cache[name]

        secrets = self._load_all()
        value = secrets.get(name)
        if value:
            self._cache[name] = value
        return value

    def delete(self, name: str) -> bool:
        """Delete a secret."""
        secrets = self._load_all()
        if name not in secrets:
            return False
        del secrets[name]
        self._save_all(secrets)
        self._cache.pop(name, None)
        log.info("secret_deleted", name=name)
        return True

    def list_keys(self) -> list[str]:
        """Return the names of all stored secrets (never values)."""
        return list(self._load_all().keys())

    def rotate_master_key(self) -> None:
        """Re-derive master key with new salt, re-encrypt all secrets."""
        secrets = self._load_all()
        # Generate new salt
        new_salt = os.urandom(32)
        with open(self._salt_path, "wb") as f:
            f.write(new_salt)
        # Re-derive cipher with new salt
        self._fernet = self._derive_cipher(new_salt)
        self._cache.clear()
        # Re-save all secrets under new cipher
        self._save_all(secrets)
        log.info("master_key_rotated")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _initialize_cipher(self) -> None:
        """Load or create the encryption salt and derive the master key."""
        self._salt_path.parent.mkdir(parents=True, exist_ok=True)

        if self._salt_path.exists():
            with open(self._salt_path, "rb") as f:
                salt = f.read()
        else:
            salt = os.urandom(32)
            with open(self._salt_path, "wb") as f:
                f.write(salt)
            log.info("secrets_salt_created")

        self._fernet = self._derive_cipher(salt)

    def _derive_cipher(self, salt: bytes) -> Fernet:
        """Derive Fernet cipher from machine UUID + salt using PBKDF2."""
        machine_id = self._get_machine_id().encode("utf-8")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
            backend=default_backend(),
        )
        key = base64.urlsafe_b64encode(kdf.derive(machine_id))
        return Fernet(key)

    def _get_machine_id(self) -> str:
        """Get a stable machine identifier for key derivation."""
        # Try Windows machine GUID first
        if platform.system() == "Windows":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography"
                ) as key:
                    machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                    return machine_guid
            except Exception:
                pass

        # Fallback: MAC address hash
        mac = uuid.getnode()
        return hashlib.sha256(str(mac).encode()).hexdigest()

    def _load_all(self) -> dict[str, str]:
        """Decrypt and load all secrets from disk."""
        if not self._secrets_path.exists():
            return {}

        try:
            with open(self._secrets_path, "rb") as f:
                encrypted = f.read()
            decrypted = self._fernet.decrypt(encrypted)
            return json.loads(decrypted.decode("utf-8"))
        except InvalidToken:
            log.error("secrets_decryption_failed",
                      message="Invalid key or corrupted secrets file")
            return {}
        except Exception as e:
            log.error("secrets_load_error", error=str(e))
            return {}

    def _save_all(self, secrets: dict[str, str]) -> None:
        """Encrypt and save all secrets to disk."""
        self._secrets_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            plaintext = json.dumps(secrets).encode("utf-8")
            encrypted = self._fernet.encrypt(plaintext)
            with open(self._secrets_path, "wb") as f:
                f.write(encrypted)
        except Exception as e:
            log.error("secrets_save_error", error=str(e))
            raise SecretsError(f"Failed to save secrets: {e}") from e
