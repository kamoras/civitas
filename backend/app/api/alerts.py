import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AlertSubscription

router = APIRouter(prefix="/alerts")

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")


def _unsubscribe_token(email: str) -> str:
    return hashlib.sha256(f"{email}{settings.DIGEST_SECRET}".encode()).hexdigest()


class SubscribeRequest(BaseModel):
    email: str
    topics: list[str] = []
    senators: list[str] = []


@router.post("/subscribe")
def subscribe(req: SubscribeRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    topics = [t for t in req.topics if isinstance(t, str)][:20]
    senators = [s for s in req.senators if isinstance(s, str)][:20]

    sub = db.query(AlertSubscription).filter(AlertSubscription.email == email).first()
    if sub:
        sub.topics = json.dumps(topics)
        sub.senators = json.dumps(senators)
        sub.active = True
    else:
        sub = AlertSubscription(
            email=email,
            topics=json.dumps(topics),
            senators=json.dumps(senators),
        )
        db.add(sub)

    db.commit()
    return JSONResponse(content={
        "ok": True,
        "message": "Subscribed! You'll receive a weekly digest.",
        "unsubscribe_token": _unsubscribe_token(email),
    })


@router.delete("/unsubscribe")
def unsubscribe(token: str = Query(...), db: Session = Depends(get_db)):
    subs = db.query(AlertSubscription).filter(AlertSubscription.active == True).all()
    matched = next(
        (s for s in subs if _unsubscribe_token(s.email) == token),
        None,
    )
    if not matched:
        # Return success HTML either way — don't leak whether address exists
        return HTMLResponse(content=_unsubscribe_html("You have been unsubscribed."), status_code=200)
    matched.active = False
    db.commit()
    return HTMLResponse(content=_unsubscribe_html("You have been unsubscribed from Civitas digests."), status_code=200)


def _unsubscribe_html(message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Civitas — Unsubscribed</title>
<style>body{{font-family:monospace;background:#0a0a0a;color:#00ff88;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0;}}
.box{{border:1px solid #00ff8840;padding:2rem 3rem;max-width:400px;text-align:center;}}
a{{color:#00e5ff;text-decoration:none;}}a:hover{{text-decoration:underline;}}</style>
</head>
<body><div class="box">
<p style="font-size:1.1rem;">{message}</p>
<p><a href="/">← Return to Civitas</a></p>
</div></body></html>"""
