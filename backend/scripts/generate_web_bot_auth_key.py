"""Make this instance's Web Bot Auth signing key, once (app/web_bot_auth.py).

    python scripts/generate_web_bot_auth_key.py --env-file /path/to/.env

Appends WEB_BOT_AUTH_KEY=<base64url Ed25519 seed> to the env file (created
0600 if missing), refusing if the file already sets one: replacing a key is
a rotation, done on purpose (docs/operations.md). The private key is never
printed. What it prints is public: the key's JWK and its thumbprint (the
keyid every signature names), which go into the bot-directory applications.
Redeploy afterwards so both backend services read it.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web_bot_auth import ENV_VAR, load_key, public_x, thumbprint  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", required=True, type=Path, help="the instance's .env (never a tracked file)")
    env_file = parser.parse_args().env_file

    existing = env_file.read_text() if env_file.exists() else ""
    if any(line.split("=", 1)[0].strip() == ENV_VAR and line.split("=", 1)[1:] != [""]
           for line in existing.splitlines() if "=" in line and not line.lstrip().startswith("#")):
        print(f"{env_file} already sets {ENV_VAR}; remove it first to rotate (docs/operations.md).", file=sys.stderr)
        return 1

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    value = base64.urlsafe_b64encode(seed).rstrip(b"=").decode()
    assert load_key(value) is not None

    fd = os.open(env_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as out:
        out.write(("" if not existing or existing.endswith("\n") else "\n") + f"{ENV_VAR}={value}\n")

    x = public_x(key)
    print(f"Wrote {ENV_VAR} to {env_file}. Public key (safe to share):")
    print(json.dumps({"kty": "OKP", "crv": "Ed25519", "kid": thumbprint(x), "x": x, "use": "sig"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
