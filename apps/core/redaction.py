import hashlib
import re
from collections.abc import Mapping, Sequence

SECRET_MARKERS = (
    "password",
    "secret",
    "token",
    "signature",
    "authorization",
    "cookie",
    "code_hash",
)
PERSONAL_MARKERS = (
    "email",
    "phone",
    "chat_id",
    "user_id",
    "username",
    "first_name",
    "last_name",
    "client_name",
    "text",
    "caption",
    "body",
    "question",
    "task",
)
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _masked_identifier(value):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def sanitize_log_data(value, key=""):
    normalized_key = str(key).lower()
    if any(marker in normalized_key for marker in SECRET_MARKERS):
        return "[скрыто]"
    if any(marker in normalized_key for marker in PERSONAL_MARKERS):
        return _masked_identifier(value) if value not in (None, "") else ""
    if isinstance(value, Mapping):
        return {str(item_key): sanitize_log_data(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_log_data(item) for item in value]
    if isinstance(value, str):
        return EMAIL_PATTERN.sub("[email скрыт]", value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]
