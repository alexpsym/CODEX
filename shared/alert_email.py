from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Mapping, Optional


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def email_readiness() -> dict[str, object]:
    enabled = _env_bool("ALERT_EMAIL_ENABLED", False)
    required = {
        "ALERT_EMAIL_TO": str(os.getenv("ALERT_EMAIL_TO") or "").strip(),
        "ALERT_EMAIL_FROM": str(os.getenv("ALERT_EMAIL_FROM") or "").strip(),
        "ALERT_SMTP_HOST": str(os.getenv("ALERT_SMTP_HOST") or "").strip(),
        "ALERT_SMTP_USERNAME": str(os.getenv("ALERT_SMTP_USERNAME") or "").strip(),
        "ALERT_SMTP_PASSWORD": str(os.getenv("ALERT_SMTP_PASSWORD") or ""),
    }
    missing = [name for name, value in required.items() if not value]
    port_raw = str(os.getenv("ALERT_SMTP_PORT") or "587").strip()
    try:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        port = None
        missing.append("ALERT_SMTP_PORT (valid 1-65535 value)")
    ready = bool(enabled and not missing)
    if not enabled:
        detail = "Email alerts are disabled; set ALERT_EMAIL_ENABLED=true to enable them."
    elif missing:
        detail = "Email alerts are not configured; set " + ", ".join(missing) + "."
    else:
        detail = "Email alerts are configured."
    return {
        "enabled": enabled,
        "ready": ready,
        "missing": missing,
        "detail": detail,
        "port": port,
        "use_tls": _env_bool("ALERT_SMTP_USE_TLS", True),
    }


def _event_body(event: Optional[Mapping[str, object]], fallback_message: str) -> str:
    payload = dict(event or {})
    trigger_time = str(payload.get("trigger_time") or "").strip()
    if not trigger_time:
        trigger_time = datetime.now(timezone.utc).isoformat()
    fields = (
        ("Alert source", payload.get("source")),
        ("Symbol", payload.get("symbol")),
        ("Alert type", payload.get("alert_type")),
        ("Condition", payload.get("condition")),
        ("Current value", payload.get("current_value")),
        ("Custom message", payload.get("custom_message")),
        ("Trigger time (UTC)", trigger_time),
    )
    lines = [f"{label}: {value}" for label, value in fields if value not in (None, "")]
    if fallback_message:
        lines.extend(("", "Alert details:", fallback_message))
    return "\n".join(lines)


def send_alert_email(
    subject: str,
    message: str,
    *,
    event: Optional[Mapping[str, object]] = None,
) -> dict[str, object]:
    readiness = email_readiness()
    if not readiness["ready"]:
        return {
            "configured": False,
            "sent": False,
            "detail": readiness["detail"],
        }

    sender = str(os.environ["ALERT_EMAIL_FROM"]).strip()
    recipients = [
        value.strip()
        for value in str(os.environ["ALERT_EMAIL_TO"]).replace(";", ",").split(",")
        if value.strip()
    ]
    email = EmailMessage()
    email["Subject"] = str(subject or "Trading alert")[:200]
    email["From"] = sender
    email["To"] = ", ".join(recipients)
    email.set_content(_event_body(event, str(message or "")))

    host = str(os.environ["ALERT_SMTP_HOST"]).strip()
    port = int(readiness["port"])
    username = str(os.environ["ALERT_SMTP_USERNAME"]).strip()
    password = str(os.environ["ALERT_SMTP_PASSWORD"])
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if readiness["use_tls"]:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(email)
        return {"configured": True, "sent": True, "detail": "Email sent successfully."}
    except Exception as exc:
        return {
            "configured": True,
            "sent": False,
            "detail": f"Email delivery failed ({type(exc).__name__}).",
        }
