"""Alert delivery.

An alert on a screen only helps an officer who is looking at the screen. This
module pushes alerts outward - email to a duty desk, webhooks into whatever the
department already runs - so a watchlist hit reaches someone who can act.

Three properties matter for a policing system and are enforced here:

  * Delivery never blocks or breaks detection. Everything runs on a worker
    thread and every failure is caught and recorded.
  * Repeat sightings of the same vehicle do not spam. A vehicle that sits in
    a camera's view produces one notification, not one per frame.
  * Only alerts above a configured severity are sent outward, so the channel
    stays worth reading.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import smtplib
import threading
import time
from dataclasses import dataclass, field
from email.message import EmailMessage

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass
class NotifyConfig:
    """Delivery settings. Credentials come from the environment only."""
    enabled: bool = field(default_factory=lambda: os.getenv("NETRA_NOTIFY", "0") == "1")
    min_severity: str = field(default_factory=lambda: os.getenv("NETRA_NOTIFY_MIN", "high"))
    #: seconds during which repeat alerts for the same plate are suppressed
    cooldown_s: float = field(default_factory=lambda: float(os.getenv("NETRA_NOTIFY_COOLDOWN", "300")))

    smtp_host: str = field(default_factory=lambda: os.getenv("NETRA_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("NETRA_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("NETRA_SMTP_USER", ""))
    smtp_pass: str = field(default_factory=lambda: os.getenv("NETRA_SMTP_PASS", ""))
    mail_from: str = field(default_factory=lambda: os.getenv("NETRA_MAIL_FROM", ""))
    mail_to: str = field(default_factory=lambda: os.getenv("NETRA_MAIL_TO", ""))

    webhook_url: str = field(default_factory=lambda: os.getenv("NETRA_WEBHOOK", ""))

    def meets(self, severity: str) -> bool:
        return SEVERITY_ORDER.get(severity, 1) >= SEVERITY_ORDER.get(self.min_severity, 2)


def render_email(alert: dict) -> tuple[str, str]:
    """Subject and body for one alert. Plain text: it has to be readable on a
    duty phone with a bad connection."""
    plate = alert.get("plate_watchlist") or alert.get("plate_observed") or "unknown"
    sev = (alert.get("severity") or "medium").upper()
    subject = f"[NETRA {sev}] Watchlist hit: {plate} at {alert.get('camera_id')}"

    reasons = alert.get("reasons") or {}
    reason_lines = "\n".join(
        f"  - {name}: {v.get('score')} - {v.get('detail')}"
        for name, v in reasons.items())

    body = f"""WATCHLIST MATCH

Vehicle       : {plate}
Observed as   : {alert.get('plate_observed') or 'not read'}
Camera        : {alert.get('camera_id')} {alert.get('camera_name') or ''}
Time (UTC)    : {alert.get('at')}
Category      : {alert.get('category') or 'unspecified'}
Case reference: {alert.get('case_ref') or 'none'}
Severity      : {sev}

Match type    : {alert.get('match_type')}
Confidence    : {alert.get('score')}

Why this matched:
{reason_lines or '  (no detail recorded)'}

This is an automated alert from the NETRA CCTV analytics platform.
Confidence scores are advisory. Verify before acting.
"""
    return subject, body


class Notifier:
    """Delivers alerts outward on a worker thread."""

    def __init__(self, cfg: NotifyConfig | None = None):
        self.cfg = cfg or NotifyConfig()
        self.queue: queue.Queue = queue.Queue(maxsize=500)
        self._recent: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"queued": 0, "sent_email": 0, "sent_webhook": 0,
                      "suppressed": 0, "failed": 0}

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="notifier", daemon=True)
        self._thread.start()
        log.info("notifier started (enabled=%s, min_severity=%s)",
                 self.cfg.enabled, self.cfg.min_severity)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, alert: dict) -> None:
        """Queue an alert for outward delivery. Never raises, never blocks."""
        if not self.cfg.enabled:
            return
        if not self.cfg.meets(alert.get("severity") or "medium"):
            return

        key = alert.get("plate_watchlist") or alert.get("plate_observed") or "?"
        now = time.time()
        last = self._recent.get(key, 0.0)
        if now - last < self.cfg.cooldown_s:
            self.stats["suppressed"] += 1
            return
        self._recent[key] = now

        try:
            self.queue.put_nowait(alert)
            self.stats["queued"] += 1
        except queue.Full:
            self.stats["failed"] += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                alert = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            for deliver in (self._send_email, self._send_webhook):
                try:
                    deliver(alert)
                except Exception:
                    self.stats["failed"] += 1
                    log.exception("alert delivery failed")

    def _send_email(self, alert: dict) -> None:
        c = self.cfg
        if not (c.smtp_host and c.mail_to and c.mail_from):
            return
        subject, body = render_email(alert)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.mail_from
        msg["To"] = c.mail_to
        msg.set_content(body)

        with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=20) as s:
            s.starttls()
            if c.smtp_user:
                s.login(c.smtp_user, c.smtp_pass)
            s.send_message(msg)
        self.stats["sent_email"] += 1
        log.info("alert emailed: %s", subject)

    def _send_webhook(self, alert: dict) -> None:
        if not self.cfg.webhook_url:
            return
        import requests
        requests.post(self.cfg.webhook_url, json=alert, timeout=15)
        self.stats["sent_webhook"] += 1


NOTIFIER = Notifier()


def _self_check() -> None:
    """Verify severity gating and repeat suppression, the two rules that decide
    whether a duty desk gets a useful channel or an unreadable one."""
    cfg = NotifyConfig()
    cfg.enabled = True
    cfg.min_severity = "high"
    cfg.cooldown_s = 60
    n = Notifier(cfg)

    n.submit({"severity": "low", "plate_watchlist": "GJ01AA0001"})
    assert n.queue.qsize() == 0, "low severity must not be sent"

    n.submit({"severity": "critical", "plate_watchlist": "GJ01AA0002"})
    assert n.queue.qsize() == 1, "critical must be queued"

    n.submit({"severity": "critical", "plate_watchlist": "GJ01AA0002"})
    assert n.queue.qsize() == 1, "repeat within cooldown must be suppressed"
    assert n.stats["suppressed"] == 1

    n.submit({"severity": "high", "plate_watchlist": "GJ01AA0003"})
    assert n.queue.qsize() == 2, "a different vehicle must still get through"

    subject, body = render_email({
        "plate_watchlist": "GJ01AB1234", "plate_observed": "GJ01AB12__",
        "camera_id": "cam12", "severity": "critical", "match_type": "partial",
        "score": 0.71, "at": "2026-09-04T21:00:00Z", "category": "stolen",
        "reasons": {"plate": {"score": 0.8, "detail": "8/10 characters agree"}},
    })
    assert "GJ01AB1234" in subject and "CRITICAL" in subject, subject
    assert "8/10 characters agree" in body, body

    print("notify self-check passed")


if __name__ == "__main__":
    _self_check()
