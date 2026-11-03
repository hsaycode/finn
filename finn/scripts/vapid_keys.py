"""Make the key pair that turns on phone notifications. Run once, and keep the output out of the repo.

    uv run --extra push python scripts/vapid_keys.py
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
private = key.private_numbers().private_value.to_bytes(32, "big")
print("Add these as environment variables on your host:\n")
print(f"VAPID_PUBLIC_KEY={b64(public)}")
print(f"VAPID_PRIVATE_KEY={b64(private)}")
print("VAPID_CONTACT=mailto:you@example.com")
