import base64
import hashlib
import hmac
import os
import struct
import time


def generate_totp_secret():
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _decode_secret(secret):
    normalized = "".join((secret or "").split()).upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def totp_code(secret, for_time=None, interval=30, digits=6):
    key = _decode_secret(secret)
    timestamp = time.time() if for_time is None else for_time
    counter = int(timestamp // interval)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def verify_totp(secret, code, window=1, interval=30, digits=6):
    candidate = "".join((code or "").split())
    if not candidate.isdigit():
        return False
    now = time.time()
    for step in range(-window, window + 1):
        expected = totp_code(secret, now + step * interval, interval=interval, digits=digits)
        if hmac.compare_digest(expected, candidate.zfill(digits)):
            return True
    return False
