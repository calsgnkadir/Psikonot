"""
core/totp.py — PsikoNot · TOTP Module
===================================================
Provides utilities for two-factor authentication (2FA) using Time-Based
One-Time Passwords (TOTP). Compatible with Google Authenticator, Authy, etc.
"""

import pyotp
import qrcode
import io
import base64

def generate_totp_secret() -> str:
    """Generates a random base32 secret."""
    return pyotp.random_base32()

def get_totp_uri(username: str, secret: str) -> str:
    """Generates the provisioning URI for Google Authenticator."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="PsikoNot")

def get_totp_qr_base64(uri: str) -> str:
    """Generates a QR code image of the URI and returns it as a base64 Data URL."""
    qr = qrcode.QRCode(version=1, box_size=4, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"

def verify_totp(secret: str, code: str) -> bool:
    """Verifies a 6-digit TOTP code with drift window of 1 interval."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)
