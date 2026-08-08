"""Encryption for the credentials stored in config.json.

Sidecarr holds API keys and OAuth tokens for several services. Writing them in
the clear means they leak through every ordinary accident: a config file pasted
into a forum thread, a backup copied somewhere less private, a screenshot of a
text editor.

What this protects against, honestly:

* ``SIDECARR_SECRET_KEY`` set - the key lives outside the config volume, so a
  copy of ``/config`` on its own is useless. This is the stronger setup.
* No env var - a random key is generated into ``/config/secret.key`` with mode
  0600. That still defeats a stray copy of ``config.json``, but anyone holding
  the whole volume holds the key too. It is obfuscation at that point, not
  secrecy, and it is the default because the alternative is refusing to start.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"
_PBKDF2_ROUNDS = 200_000


class SecretBox:
    """Encrypts and decrypts individual config values."""

    def __init__(self, key_dir: Path, passphrase: str | None = None) -> None:
        self.key_dir = Path(key_dir)
        self._fernet = Fernet(self._resolve_key(passphrase))

    # -- key material ------------------------------------------------------- #

    def _resolve_key(self, passphrase: str | None) -> bytes:
        if passphrase:
            return self._derive(passphrase)
        return self._key_file()

    def _derive(self, passphrase: str) -> bytes:
        """Stretch a human-chosen passphrase into a Fernet key."""
        salt_path = self.key_dir / "secret.salt"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            self._write_private(salt_path, salt)
        derived = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, _PBKDF2_ROUNDS)
        return base64.urlsafe_b64encode(derived)

    def _key_file(self) -> bytes:
        key_path = self.key_dir / "secret.key"
        if key_path.exists():
            key = key_path.read_bytes().strip()
            if key:
                return key
        key = Fernet.generate_key()
        self._write_private(key_path, key)
        log.info(
            "Generated an encryption key at %s. Set SIDECARR_SECRET_KEY to keep the "
            "key outside this volume instead.",
            key_path,
        )
        return key

    @staticmethod
    def _write_private(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # -- values -------------------------------------------------------------- #

    def encrypt(self, value: str) -> str:
        if not value or self.is_encrypted(value):
            return value
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        """Decrypt a stored value, passing plaintext through unchanged.

        Config files written before encryption existed hold plaintext, and they
        must keep working; they are re-written encrypted on the next save.
        """
        if not self.is_encrypted(value):
            return value
        try:
            return self._fernet.decrypt(value[len(PREFIX):].encode()).decode()
        except InvalidToken:
            log.error(
                "A stored credential could not be decrypted. The encryption key has "
                "changed or been lost; re-enter the affected keys in Settings."
            )
            return ""

    @staticmethod
    def is_encrypted(value: str) -> bool:
        return isinstance(value, str) and value.startswith(PREFIX)


# Config paths holding a credential. Each entry is a tuple of keys, and "*"
# matches every key at that level.
SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("trakt", "client_secret"),
    ("trakt", "accounts", "*", "access_token"),
    ("trakt", "accounts", "*", "refresh_token"),
    ("tmdb", "api_key"),
    ("mdblist", "api_key"),
    ("plex", "token"),
    ("radarr", "api_key"),
    ("sonarr", "api_key"),
)


def transform_secrets(data: dict, fn) -> dict:
    """Apply ``fn`` to every credential in a config dict, in place."""
    for path in SECRET_PATHS:
        _apply(data, path, fn)
    return data


def _apply(node, path: tuple[str, ...], fn) -> None:
    if not isinstance(node, dict):
        return
    head, rest = path[0], path[1:]

    if head == "*":
        for value in node.values():
            _apply(value, rest, fn) if rest else None
        return

    if not rest:
        current = node.get(head)
        if isinstance(current, str) and current:
            node[head] = fn(current)
        return

    child = node.get(head)
    if rest[0] == "*" and isinstance(child, dict):
        for value in child.values():
            _apply(value, rest[1:], fn)
    else:
        _apply(child, rest, fn)
