"""رمز الدخول (PIN) للتطبيق. بنخزّن بصمة مشفّرة منه بس، مش الرمز نفسه."""

import hashlib
import hmac
import secrets
import time

_ITERATIONS = 300_000


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_pin(pin: str, stored: str) -> bool:
    salt, digest = stored.split("$", 1)
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return hmac.compare_digest(candidate, digest)


class LoginLimiter:
    """بعد 5 محاولات غلط، بنقفل الدخول 5 دقايق."""

    def __init__(self, max_fails: int = 5, lock_seconds: int = 300):
        self.max_fails = max_fails
        self.lock_seconds = lock_seconds
        self._fails: dict[str, tuple[int, float]] = {}

    def locked_for(self, key: str) -> int:
        fails, until = self._fails.get(key, (0, 0.0))
        if fails < self.max_fails:
            return 0
        remaining = int(until - time.time())
        if remaining <= 0:
            self.reset(key)
            return 0
        return remaining

    def fail(self, key: str) -> None:
        fails, _ = self._fails.get(key, (0, 0.0))
        fails += 1
        self._fails[key] = (fails, time.time() + self.lock_seconds)

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)
