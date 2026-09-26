"""Best-effort operator alerts for code running mid-pipeline."""

import logging

logger = logging.getLogger(__name__)


def safe_ops_alert(subject: str, body: str, *, dedupe_key: str) -> None:
    """Send an ops alert, never letting it raise.

    Pipeline code alerts about a problem it is otherwise tolerating; a
    failure to *send* that alert must not take the nightly run down with
    it. ops_alerts is imported lazily, the same pattern scheduler.py uses,
    so importing this module pulls in nothing at load time.
    """
    try:
        from app.ops_alerts import send_ops_alert
        send_ops_alert(subject, body, dedupe_key=dedupe_key)
    except Exception:
        logger.exception("Failed to send ops alert: %s", subject)
