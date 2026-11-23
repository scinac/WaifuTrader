"""Password hashing, TOTP, encryption at rest and session tokens."""
import hashlib
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet

_hasher = PasswordHasher()

TOTP_ISSUER = "Trading Terminal"
TOTP_STEP = 30


# --- passwords ---------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")


# --- encryption at rest ------------------------------------------------------
class Vault:
    """Encrypts small secrets (TOTP seeds, broker API keys) with the server master key."""

    def __init__(self, master_key: str):
        self._f = Fernet(master_key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()


def generate_master_key() -> str:
    return Fernet.generate_key().decode()


# --- TOTP --------------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)


def verify_totp(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched time step if the code is valid and not replayed, else None.

    Accepts the current step and one step either side (clock drift).
    """
    if not code or not code.isdigit() or len(code) != 6:
        return None
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(now // TOTP_STEP)
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.at(step * TOTP_STEP), code):
            if last_step is not None and step <= last_step:
                return None  # replay of an already-used code
            return step
    return None


# --- tokens ------------------------------------------------------------------
def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
