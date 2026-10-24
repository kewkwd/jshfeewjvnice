import functools
import logging
import math
import time

log = logging.getLogger(__name__)


class PermanentFetchError(RuntimeError):
    """The content cannot be fetched (private, deleted, unsupported); retrying won't help."""
    retryable = False

DEFAULT_RETRY_BACKOFF = 2.0   # Standardwert von cfg.retry_backoff: bei diesem Wert bleiben die Wartezeiten unverändert


def _runtime_settings(args, kwargs, times, backoff):
    """Anzahl der Versuche und Wartefaktor zur Laufzeit aus ctx.cfg (max_retries / retry_backoff).

    Fehlen die Einstellungen oder sind sie ungültig (z. B. Attrappen), gelten die Werte des Dekorators.
    `backoff` im Dekorator ist die Basis je Stelle (YouTube 2.0, Instagram 3.0, ...); cfg.retry_backoff skaliert sie
    relativ zum Standard 2.0, bei Standardwerten ändert sich also nichts.
    """
    ctx = kwargs.get('ctx')
    if ctx is None:
        for item in reversed(args[1:]):
            if hasattr(item, 'cfg'):
                ctx = item
                break
    cfg = getattr(ctx, 'cfg', None)
    if cfg is None:
        return times, 1.0
    count = getattr(cfg, 'max_retries', None)
    if type(count) is int and count >= 1:
        times = count
    factor = 1.0
    scale = getattr(cfg, 'retry_backoff', None)
    if isinstance(scale, (int, float)) and not isinstance(scale, bool) and math.isfinite(scale) and scale > 0:
        factor = scale / DEFAULT_RETRY_BACKOFF
    return times, factor


def with_retry(times=3, backoff=2.0, exceptions=(Exception,)):
    if type(times) is not int or times < 1: raise ValueError('times muss mindestens 1 sein')
    if not isinstance(backoff, (int, float)) or not math.isfinite(backoff) or backoff <= 0:
        raise ValueError('backoff muss positiv sein')
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            limit, factor = _runtime_settings(args, kwargs, times, backoff)
            for attempt in range(1, limit + 1):
                try: return fn(*args, **kwargs)
                except exceptions as error:
                    from .errors import is_hopeless  # z. B. lokaler Proxy verweigert: sofort enden
                    from .deadline import expired  # Zeitlimit abgelaufen: nicht mehr wiederholen
                    if (is_hopeless(error) or expired()
                            or isinstance(error, (ValueError, TypeError, FileNotFoundError))
                            or attempt == limit): raise
                    wait = backoff ** attempt * factor
                    log.warning('%s fehlgeschlagen (%s/%s) - retry in %.1fs', fn.__name__, attempt, limit, wait)
                    time.sleep(wait)
        return wrapper
    return deco
