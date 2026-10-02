import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "fernet:"


def _legacy_secret_key():
    return base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()).decode("ascii")


def _keyring():
    primary = (
        getattr(settings, "AURUMWEB_TOTP_ENCRYPTION_KEY", "") or os.getenv("AURUMWEB_TOTP_ENCRYPTION_KEY", "").strip()
    )
    old_keys = list(getattr(settings, "AURUMWEB_TOTP_ENCRYPTION_OLD_KEYS", ()))
    allow_legacy = getattr(settings, "AURUMWEB_ALLOW_LEGACY_SECRET_KEY_DECRYPTION", settings.DEBUG)
    keys = [primary] if primary else []
    keys.extend(old_keys)
    if allow_legacy or not keys:
        keys.append(_legacy_secret_key())
    unique_keys = tuple(dict.fromkeys(key for key in keys if key))
    return tuple(Fernet(key.encode("ascii")) for key in unique_keys)


def _primary_fernet():
    return _keyring()[0]


def encrypt_value(value):
    if not value or value.startswith(PREFIX):
        return value
    token = _primary_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_value(value):
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value
    token = value[len(PREFIX) :].encode("ascii")
    for fernet in _keyring():
        try:
            return fernet.decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
    return ""


def rotate_encrypted_value(value):
    if not value:
        return ""
    plain_value = decrypt_value(value)
    if value.startswith(PREFIX) and not plain_value:
        raise InvalidToken("Encrypted value cannot be decrypted with the configured keyring.")
    token = _primary_fernet().encrypt(plain_value.encode("utf-8")).decode("ascii")
    return PREFIX + token
