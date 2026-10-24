"""Bounded observation-only error handoff inside the still-owned browser job."""
import math
import time

def validate_duration(value):
    if (isinstance(value,bool) or not isinstance(value,(int,float)) or
            not 0<=value<=600 or not math.isfinite(value)):
        raise ValueError('Das Diagnosefenster muss zwischen 0 und 600 Sekunden offen bleiben.')
    return value

def hold(page,ctx,seconds):
    validate_duration(seconds)
    ctx.check_cancelled()
    if not seconds:return False
    try:
        if page.is_closed():return False
    except Exception:return False
    from .registration_progress import update
    update(ctx,'review_required')
    ctx.melden('Auftrag wegen eines Fehlers angehalten. Das eigene Browserfenster bleibt '
               'höchstens '+str(int(seconds))+' Sekunden zur Prüfung offen. '
               'Es werden keine weiteren Formulare ausgefüllt oder abgesendet. '
               'Bitte den sichtbaren Schritt melden; mit Auftrag abbrechen beenden.')
    deadline=time.monotonic()+seconds
    while True:
        ctx.check_cancelled()
        try:
            if page.is_closed():break
        except Exception:break
        left=deadline-time.monotonic()
        if left<=0:break
        try:
            ctx.warte(min(250,left*1000))
        except Exception:
            ctx.check_cancelled()
            try:
                if page.is_closed():break
            except Exception:break
            raise
    return True
