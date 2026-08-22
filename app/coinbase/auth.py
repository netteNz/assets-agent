import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization


def build_rest_jwt(*, key_id: str, private_key_pem: str, method: str, host: str, path: str) -> str:
    """Build the request-bound ES256 JWT required by Coinbase App APIs."""
    now = int(time.time())
    private_key = serialization.load_pem_private_key(
        private_key_pem.replace("\\n", "\n").encode(), password=None
    )
    return jwt.encode(
        {"sub": key_id, "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method.upper()} {host}{path}"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "nonce": secrets.token_hex()},
    )

