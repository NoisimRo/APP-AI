"""Encryption utilities for sensitive data (API keys).

Uses Fernet symmetric encryption with a key from the LLM_SETTINGS_KEY
environment variable. If not set, generates a key and logs a warning.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from app.core.logging import get_logger

logger = get_logger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet cipher instance."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("LLM_SETTINGS_KEY")
    if not key:
        key = Fernet.generate_key().decode()
        logger.warning(
            "llm_settings_key_not_set",
            message="LLM_SETTINGS_KEY not set — generated ephemeral key. "
            "API keys stored in DB will be unreadable after restart. "
            "Set LLM_SETTINGS_KEY env var for persistence.",
        )
    _fernet = Fernet(key if isinstance(key, bytes) else key.encode())
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext and return plaintext."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("decrypt_failed", message="Invalid token — key may have changed")
        return ""
