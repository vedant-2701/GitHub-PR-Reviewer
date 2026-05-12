# tests/test_hmac_validator.py
import hashlib
import hmac
from unittest.mock import patch

import pytest

from app.utils.hmac_validator import validate_github_signature


SECRET = "test-webhook-secret"
PAYLOAD = b'{"action": "opened"}'


def _make_signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ── Valid signature ───────────────────────────────────────────────────────────

def test_valid_signature_returns_true():
    sig = _make_signature(PAYLOAD, SECRET)
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(PAYLOAD, sig) is True


# ── Invalid / tampered signature ──────────────────────────────────────────────

def test_wrong_secret_returns_false():
    sig = _make_signature(PAYLOAD, "wrong-secret")
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(PAYLOAD, sig) is False


def test_tampered_payload_returns_false():
    sig = _make_signature(PAYLOAD, SECRET)
    tampered = b'{"action": "deleted"}'
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(tampered, sig) is False


def test_empty_signature_header_returns_false():
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(PAYLOAD, "") is False


def test_missing_sha256_prefix_returns_false():
    raw_digest = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()
    # Send digest without "sha256=" prefix
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(PAYLOAD, raw_digest) is False


def test_unconfigured_secret_returns_false():
    sig = _make_signature(PAYLOAD, SECRET)
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = ""
        assert validate_github_signature(PAYLOAD, sig) is False


def test_sha1_signature_format_rejected():
    """GitHub also sends X-Hub-Signature (sha1). We only accept sha256."""
    sha1_digest = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha1).hexdigest()
    sha1_sig = f"sha1={sha1_digest}"
    with patch("app.utils.hmac_validator.get_settings") as mock_settings:
        mock_settings.return_value.GITHUB_WEBHOOK_SECRET = SECRET
        assert validate_github_signature(PAYLOAD, sha1_sig) is False