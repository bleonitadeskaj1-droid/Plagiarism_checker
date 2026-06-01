# ============================================
# confidential.py - Sistemi i Dokumenteve Konfidenciale
# Dokumentet enkriptohen me AES-256, perdoren vetem per krahasim
# Admini dhe profesori nuk mund t'i lexojne permbajten direkt
# ============================================

import os
import hashlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

# ── ENCRYPTION KEY ──
# Merret nga environment variable - kurre hard-coded
def get_encryption_key() -> bytes:
    secret = os.getenv("ENCRYPTION_SECRET", "change-this-in-production-please")
    salt   = os.getenv("ENCRYPTION_SALT",   "plagiarism-salt-2024").encode()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))

def get_fernet() -> Fernet:
    return Fernet(get_encryption_key())

# ── ENKRIPTIM ──
def encrypt_content(text: str) -> bytes:
    """Enkipton tekstin e dokumentit. Rezultati ruhet ne DB."""
    f = get_fernet()
    return f.encrypt(text.encode("utf-8"))

def decrypt_content(encrypted: bytes) -> str:
    """Dekipton per perdorim INTERN nga AI agjenti. Kurre nuk kthehet te frontend."""
    f = get_fernet()
    return f.decrypt(encrypted).decode("utf-8")

# ── HASH per identifikim pa ekspozuar permbajtjen ──
def hash_content(text: str) -> str:
    """Hash SHA-256 i permbajtjes - per zbulimin e kopjeve identike."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()