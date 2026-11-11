"""Password hashing and process-local login throttling; no plaintext storage."""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from threading import Lock
from time import monotonic

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class LoginConfig:
    username: str
    salt: bytes
    digest: bytes

    @classmethod
    def load(cls, setting):
        try:
            username = setting("LOGIN_USERNAME").strip()
            salt = bytes.fromhex(setting("LOGIN_SALT").strip())
            digest = bytes.fromhex(setting("LOGIN_PASSWORD_HASH").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("登录配置格式错误") from exc
        if not username or len(salt) != 16 or len(digest) != 32:
            raise ValueError("登录配置缺失或格式错误")
        return cls(username, salt, digest)

    @property
    def revision(self):
        return hashlib.sha256(self.username.encode() + self.salt + self.digest).hexdigest()

    def verify(self, username, password):
        # Bound computation/memory for untrusted input, then always hash before comparing.
        if len(username) > 128 or len(password) > 1024:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), self.salt, ITERATIONS)
        return hmac.compare_digest(actual, self.digest) and username == self.username


def make_config(username, password):
    if not username.strip() or len(username) > 128 or not 12 <= len(password) <= 1024:
        raise ValueError("账号不能为空，密码长度须为 12 至 1024 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return LoginConfig(username.strip(), salt, digest)


def valid_session(session, config, now=None):
    now = monotonic() if now is None else now
    return (session.get("revision") == config.revision
            and isinstance(session.get("until"), (int, float))
            and now < session["until"])


class LoginLimiter:
    """One shared limiter per configured account/process, including unknown usernames.

    Not persistent across restarts or replicas. A lock serializes checks so concurrent
    requests cannot bypass the failure threshold. No unbounded per-user dictionary.
    """
    def __init__(self):
        self.lock = Lock()
        self.failures = 0
        self.blocked_until = 0.0

    def attempt(self, config, username, password, now=None):
        now = monotonic() if now is None else now
        with self.lock:
            if now < self.blocked_until:
                return False, max(1, int(self.blocked_until - now) + 1)
            if self.blocked_until:
                self.failures = 0
                self.blocked_until = 0.0
            if config.verify(username, password):
                self.failures = 0
                return True, 0
            self.failures += 1
            if self.failures >= 5:
                self.blocked_until = now + 60
            return False, 0
