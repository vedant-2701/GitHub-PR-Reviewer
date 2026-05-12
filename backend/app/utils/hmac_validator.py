import hmac
import hashlib
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)


def validate_github_signature(payload: bytes, signature_header: str) -> bool:
    """
    Validate the HMAC-SHA256 signature on an incoming GitHub webhook payload.

    GitHub sends the signature in the 'X-Hub-Signature-256' header as:
        sha256=<hex-digest>

    Args:
        payload:          Raw request body bytes — must be read BEFORE JSON parsing.
        signature_header: Value of the X-Hub-Signature-256 header.

    Returns:
        True  — signature is valid.
        False — signature is missing, malformed, or does not match.

    Design note: This function returns False rather than raising so that
    the caller (the webhook router) decides the HTTP response. This makes
    it easier to test and avoids coupling HMAC logic to HTTP concerns.
    """
    settings = get_settings()

    if not signature_header:
        logger.warning("Webhook rejected — X-Hub-Signature-256 header missing")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning(
            "Webhook rejected — unexpected signature format: %s",
            signature_header[:20],
        )
        return False

    if not settings.GITHUB_WEBHOOK_SECRET:
        # This is a configuration error, not an auth error — log loudly.
        logger.error(
            "GITHUB_WEBHOOK_SECRET is not configured. "
            "All webhook requests will be rejected."
        )
        return False

    expected_digest = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    received_digest = signature_header[len("sha256="):]

    # compare_digest prevents timing attacks
    valid = hmac.compare_digest(expected_digest, received_digest)

    if not valid:
        logger.warning(
            "Webhook rejected — HMAC mismatch. "
            "Check that GITHUB_WEBHOOK_SECRET matches the GitHub App configuration."
        )

    return valid