import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import BackupCode

BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_backup_code(code):
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _format_backup_code(code):
    return f"{code[:4]}-{code[4:]}"


def generate_backup_codes(count=10):
    codes = []
    seen = set()
    while len(codes) < count:
        raw = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(8))
        if raw not in seen:
            seen.add(raw)
            codes.append(_format_backup_code(raw))
    return codes


def replace_backup_codes(user, count=10):
    codes = generate_backup_codes(count=count)
    BackupCode.objects.filter(user=user).delete()
    BackupCode.objects.bulk_create(
        BackupCode(user=user, code_hash=make_password(normalize_backup_code(code))) for code in codes
    )
    return codes


def consume_backup_code(user, code):
    normalized = normalize_backup_code(code)
    if len(normalized) != 8:
        return False

    for backup_code in BackupCode.objects.filter(user=user, used_at__isnull=True):
        if check_password(normalized, backup_code.code_hash):
            backup_code.used_at = timezone.now()
            backup_code.save(update_fields=("used_at",))
            return True
    return False


def backup_code_stats(user):
    codes = BackupCode.objects.filter(user=user)
    total = codes.count()
    remaining = codes.filter(used_at__isnull=True).count()
    return {"total": total, "remaining": remaining, "used": total - remaining}
