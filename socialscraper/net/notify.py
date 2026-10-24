"""Optional notification delivery; failures must not mask the original result."""
import datetime
import json
import logging
from pathlib import Path
import urllib.request

log = logging.getLogger(__name__)
_cfg = None
_SENSITIVE = ("password", "passwd", "secret", "token", "cookie", "credential", "authorization", "webhook")


def configure(cfg):
    global _cfg
    _cfg = cfg


def _safe(value):
    if isinstance(value, dict):
        return {str(key): "[redacted]" if any(part in str(key).lower() for part in _SENSITIVE)
                or str(key).lower() in ("pass", "pw") else _safe(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return "[unsupported]"


def notify(event, message, **extra):
    from .accountproxy import scrub   # Proxy-Passwörter nie in Meldungen
    message = scrub(message)
    payload = _safe({**extra, "event": event, "message": message,
                     "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    log.warning("[%s] %s", event, message)
    if _cfg is None:
        return
    if getattr(_cfg, "notify_file", ""):
        try:
            path = Path(_cfg.notify_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n")
        except (OSError, ValueError, TypeError):
            log.warning("Benachrichtigungsdatei konnte nicht geschrieben werden")
    if getattr(_cfg, "notify_webhook", ""):
        try:
            request = urllib.request.Request(_cfg.notify_webhook,
                       data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
        except Exception:
            log.warning("Webhook konnte nicht gesendet werden")
