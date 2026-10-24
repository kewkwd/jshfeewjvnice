import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000,
           "t": 1_000, "mio": 1_000_000, "tsd": 1_000}

def parse_short_number(s) -> int | None:
    """'1.2K' -> 1200, '3,4 Mio.' -> 3400000, '1 234' -> 1234"""
    if s is None or isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, float):
        return int(s) if math.isfinite(s) else None
    text = re.sub(r"\s+", "", str(s).strip().lower())
    match = re.fullmatch(r"(-?\d+(?:[.,]\d+)*)([a-z]*)(?:\.)?", text)
    if not match:
        return None
    number, suffix = match.groups()
    if suffix and suffix not in _SUFFIX:
        return None
    if "." in number and "," in number:
        # The final separator is the decimal separator in either locale.
        decimal = "." if number.rfind(".") > number.rfind(",") else ","
        number = number.replace("," if decimal == "." else ".", "")
        number = number.replace(decimal, ".")
    elif not suffix and re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", number):
        number = number.replace(".", "").replace(",", "")
    else:
        number = number.replace(",", ".")
    try:
        return int(Decimal(number) * _SUFFIX.get(suffix, 1))
    except (InvalidOperation, ValueError):
        return None


def matches_host(url: str, *domains: str) -> bool:
    """Match a real HTTP(S) host, never a domain embedded in another URL."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        parsed.port  # Reject malformed or out-of-range ports before browser navigation.
    except ValueError:
        return False
    return "\\" not in parsed.netloc and parsed.scheme.lower() in ("http", "https") and any(
        host == domain or host.endswith("." + domain) for domain in domains
    )


def comment_id(video_id, author, text, supplied=None) -> str:
    """Use the source ID or a stable, video-scoped fallback for repeated DOM rows."""
    if supplied is not None and str(supplied).strip():
        return str(supplied)
    payload = json.dumps([str(video_id), author or "", text or ""],
                         ensure_ascii=False, separators=(",", ":"))
    return "text:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def comment_limit(cfg) -> int:
    limit = cfg.max_comments
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("max_comments muss eine nichtnegative ganze Zahl sein")
    return limit


# ---------- Videolänge ----------

DEFAULT_SHORT_S, DEFAULT_LONG_S = 60, 600
LENGTH_CLASSES = (('short', 'kurz'), ('medium', 'mittel'), ('long', 'lang'))


def format_duration(seconds):
    """Länge in Sekunden als „m:ss“ oder „h:mm:ss“; leer, wenn keine Länge bekannt ist."""
    if seconds is None or isinstance(seconds, bool):
        return ''
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError, OverflowError):
        return ''
    if total < 0:
        return ''
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


def duration_class(seconds, short=DEFAULT_SHORT_S, long=DEFAULT_LONG_S):
    """'short' (bis `short` Sekunden) | 'medium' | 'long' (über `long` Sekunden); None, wenn keine Länge bekannt ist."""
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:
        return None
    if value <= short:
        return 'short'
    return 'medium' if value <= long else 'long'


def ro_connect(db_path, timeout=5):
    """Schreibgeschützte SQLite-Verbindung. Der URI-Modus (`mode=ro`) kennt keine Netzwerkpfade (\\\\server\\freigabe):
    SQLite lehnt dort die Angabe des Servers ab. Dann eine normale Verbindung, die nur lesen darf; eine fehlende Datei
    wird – wie im URI-Modus – nicht angelegt."""
    import pathlib
    import sqlite3
    path = pathlib.Path(db_path)
    text = str(path)
    if text.startswith('\\\\') or text.startswith('//'):
        if not path.is_file():
            raise sqlite3.OperationalError('unable to open database file')
        db = sqlite3.connect(text, timeout=timeout)
        db.execute('PRAGMA query_only=ON')
        return db
    return sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=timeout)
