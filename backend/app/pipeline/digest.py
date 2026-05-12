"""Weekly email digest — assembles content from existing DB data, no new LLM calls."""

import json
import logging
import smtplib
import hashlib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import AlertSubscription, ExploreDocument, NationalMonitor, Senator

logger = logging.getLogger(__name__)


def _unsubscribe_token(email: str) -> str:
    return hashlib.sha256(f"{email}{settings.DIGEST_SECRET}".encode()).hexdigest()


def _base_url() -> str:
    return "https://civitas.mack.pub"


def send_weekly_digests() -> int:
    """Send weekly digest to all active subscribers. Returns number of emails sent."""
    if not settings.SMTP_HOST:
        logger.info("Digest skipped — SMTP_HOST not configured")
        return 0

    db = SessionLocal()
    try:
        return _send_all(db)
    finally:
        db.close()


def _send_all(db: Session) -> int:
    subs = db.query(AlertSubscription).filter(AlertSubscription.active == True).all()
    if not subs:
        return 0

    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Fetch data used across all digests once
    open_docs = (
        db.query(ExploreDocument)
        .filter(
            ExploreDocument.comment_url.isnot(None),
            ExploreDocument.comment_url != "",
            ExploreDocument.comments_close_on >= today,
        )
        .order_by(ExploreDocument.comments_close_on)
        .limit(50)
        .all()
    )

    monitors = (
        db.query(NationalMonitor)
        .filter(NationalMonitor.last_article_date >= week_ago)
        .limit(10)
        .all()
    )

    sent = 0
    for sub in subs:
        try:
            topics = json.loads(sub.topics or "[]")
            senator_ids = json.loads(sub.senators or "[]")
            body_html = _build_email(sub.email, topics, senator_ids, open_docs, monitors, db)
            _send_email(sub.email, "Your Civitas Weekly Digest", body_html)
            sub.last_sent_at = datetime.utcnow()
            sent += 1
        except Exception:
            logger.exception("Failed to send digest to %s", sub.email)

    db.commit()
    logger.info("Weekly digest: sent %d / %d", sent, len(subs))
    return sent


def _build_email(
    email: str,
    topics: list[str],
    senator_ids: list[str],
    open_docs: list,
    monitors: list,
    db: Session,
) -> str:
    base = _base_url()
    token = _unsubscribe_token(email)
    unsub_url = f"{base}/api/alerts/unsubscribe?token={token}"
    sections: list[str] = []

    # ── Senators section ─────────────────────────────────────────────
    if senator_ids:
        senators = db.query(Senator).filter(Senator.id.in_(senator_ids)).all()
        if senators:
            rows = "".join(
                f'<tr><td style="padding:6px 0"><a href="{base}/scorecard?branch=senate&state={s.state}&senator={s.id}" '
                f'style="color:#00e5ff;text-decoration:none">{s.name}</a></td>'
                f'<td style="padding:6px 0;color:#aaa;text-align:right">{s.state} · {s.party}</td></tr>'
                for s in senators
            )
            sections.append(
                f'<h2 style="color:#00ff88;font-size:14px;letter-spacing:0.1em;margin-top:24px">YOUR SENATORS</h2>'
                f'<table style="width:100%;border-collapse:collapse;font-size:13px">{rows}</table>'
                f'<p style="font-size:11px;color:#888">View their full scorecards at civitas.mack.pub/scorecard</p>'
            )

    # ── Open comment periods ─────────────────────────────────────────
    relevant_docs = open_docs
    if topics:
        topics_lower = {t.lower() for t in topics}
        relevant_docs = [
            d for d in open_docs
            if any(
                t.lower() in topics_lower
                for t in json.loads(d.policy_areas or "[]")
            )
        ] or open_docs[:5]  # fallback: first 5 if no topic match

    if relevant_docs:
        items = "".join(
            f'<li style="margin-bottom:10px">'
            f'<a href="{base}/explore/{d.id}" style="color:#00e5ff;text-decoration:none">{d.title[:80]}</a>'
            f'<br><span style="color:#888;font-size:11px">Comments close {d.comments_close_on} · '
            f'{d.agency_name or d.chamber or "Federal"}</span></li>'
            for d in relevant_docs[:6]
        )
        sections.append(
            f'<h2 style="color:#00ff88;font-size:14px;letter-spacing:0.1em;margin-top:24px">'
            f'OPEN FOR PUBLIC COMMENT</h2>'
            f'<ul style="padding-left:0;list-style:none;font-size:13px">{items}</ul>'
        )

    # ── National monitors ────────────────────────────────────────────
    if monitors:
        items = "".join(
            f'<li style="margin-bottom:10px">'
            f'<a href="{base}/action?tab=monitors" style="color:#00e5ff;text-decoration:none">{m.title[:70]}</a>'
            f'<br><span style="color:#888;font-size:11px">Updated {m.last_article_date}</span></li>'
            for m in monitors[:4]
        )
        sections.append(
            f'<h2 style="color:#00ff88;font-size:14px;letter-spacing:0.1em;margin-top:24px">'
            f'NATIONAL MONITORS — UPDATED THIS WEEK</h2>'
            f'<ul style="padding-left:0;list-style:none;font-size:13px">{items}</ul>'
        )

    if not sections:
        sections.append(
            '<p style="color:#aaa;font-size:13px">No new activity this week matching your subscriptions. '
            'Check <a href="' + base + '/action" style="color:#00e5ff">the Action Center</a> for today\'s top issues.</p>'
        )

    body = "\n".join(sections)

    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Civitas Weekly Digest</title></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:monospace;color:#ccc">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:32px 16px">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">

<tr><td style="border-bottom:1px solid #00ff8830;padding-bottom:16px;margin-bottom:16px">
<h1 style="color:#00ff88;font-size:20px;letter-spacing:0.15em;margin:0">CIVITAS</h1>
<p style="color:#666;font-size:11px;margin:4px 0 0">Weekly Digest</p>
</td></tr>

<tr><td style="padding-top:16px">
{body}
</td></tr>

<tr><td style="border-top:1px solid #00ff8820;padding-top:16px;margin-top:32px;font-size:11px;color:#555">
<p>You're receiving this because you subscribed at civitas.mack.pub.</p>
<p><a href="{unsub_url}" style="color:#888">Unsubscribe</a> ·
<a href="{base}" style="color:#888">Visit Civitas</a></p>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def _send_email(to: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        if settings.SMTP_USER:
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
        s.sendmail(msg["From"], [to], msg.as_string())
