import base64
import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _load_signing_key(raw_secret: str):
    """Coinbase issues either a PEM EC private key (ES256) or a base64 Ed25519 key (EdDSA)."""
    if raw_secret.strip().startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw_secret.replace("\\n", "\n").encode(), password=None)
        return key, "ES256"

    decoded = base64.b64decode(raw_secret)
    seed = decoded[:32]
    return Ed25519PrivateKey.from_private_bytes(seed), "EdDSA"


def build_rest_jwt(*, key_id: str, private_key_pem: str, method: str, host: str, path: str) -> str:
    """Build the request-bound JWT required by Coinbase App APIs."""
    now = int(time.time())
    private_key, algorithm = _load_signing_key(private_key_pem)
    return jwt.encode(
        {"sub": key_id, "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method.upper()} {host}{path}"},
        private_key,
        algorithm=algorithm,
        headers={"kid": key_id, "nonce": secrets.token_hex()},
    )

