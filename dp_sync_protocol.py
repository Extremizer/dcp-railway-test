"""Protocol v2 binds producer ownership, method, path and exact request bytes."""
import hashlib
import hmac
import re
import secrets
import time


class ProtocolError(Exception):
    pass


def sign(key: bytes, method: str, path: str, timestamp: int, producer_id: str, body: bytes) -> str:
    message = f"v2\n{method.upper()}\n{path}\n{timestamp}\n{producer_id}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify(key, method, path, timestamp, producer_id, body, signature):
    try:
        ts = int(str(timestamp or ""))
    except ValueError:
        raise ProtocolError("unauthorized")
    if not isinstance(producer_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", producer_id):
        raise ProtocolError("unauthorized")
    if abs(int(time.time()) - ts) > 60:
        raise ProtocolError("stale_signature")
    expected = sign(key, method, path, ts, producer_id, body)
    if not isinstance(signature, str) or not secrets.compare_digest(signature.lower(), expected):
        raise ProtocolError("unauthorized")
