from dataclasses import dataclass, field, fields, asdict
from typing import Any, Optional
import datetime as _dt

def iso(ts) -> Optional[str]:
    if ts is None: return None
    if isinstance(ts, _dt.datetime):
        return ts.replace(tzinfo=ts.tzinfo or _dt.timezone.utc).astimezone(_dt.timezone.utc).isoformat()
    try:
        return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return None

# Längste plausible Videolänge je Plattform in Sekunden. Was darüber liegt, ist meist ein Wert in Millisekunden.
MAX_DURATION_S = {'tiktok': 4 * 3600, 'instagram': 6 * 3600, 'reddit': 2 * 3600, 'x': 8 * 3600, 'youtube': 100 * 3600}
DEFAULT_MAX_DURATION_S = 48 * 3600


def normalize_duration(value, platform=None):
    """Videolänge als Zahl in SEKUNDEN (float) oder None.

    Nimmt Zahlen, Zahlentexte, „m:ss“, „h:mm:ss“ und ISO-8601 („PT1H2M3S“). Leere, negative, unendliche und
    unlesbare Werte werden zu None. Ist eine Zahl für die Plattform unmöglich lang, wird sie als Millisekunden
    gelesen (Millisekunden-Fehler abfangen); passt auch das nicht, ist sie None. Die Länge Null (Bilder, Textbeiträge)
    bleibt 0.0."""
    import math
    import re
    if value is None or isinstance(value, bool):
        return None
    seconds = None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        iso_match = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', text.upper())
        if iso_match and any(iso_match.groups()):
            d, h, m, s = (float(g) if g else 0.0 for g in iso_match.groups())
            seconds = d * 86400 + h * 3600 + m * 60 + s
        elif re.fullmatch(r'\d{1,3}(?::\d{1,2}){1,2}(?:\.\d+)?', text):
            parts = [float(p) for p in text.split(':')]
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + part
        else:
            try:
                seconds = float(text.replace(',', '.'))
            except ValueError:
                return None
    else:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    limit = MAX_DURATION_S.get(platform or '', DEFAULT_MAX_DURATION_S)
    if seconds > limit:
        seconds = seconds / 1000.0
        if seconds > limit:
            return None
    return round(seconds, 3)


class _Base:
    @classmethod
    def from_dict(cls, d: dict):
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        if extra:
            kwargs["raw"] = {**(kwargs.get("raw") or {}), **extra}
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class Video(_Base):
    platform: str
    video_id: str
    url: str
    author: Optional[str] = None
    author_id: Optional[str] = None
    caption: Optional[str] = None
    created_at: Optional[str] = None
    duration_s: Optional[float] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    save_count: Optional[int] = None
    hashtags: list = field(default_factory=list)
    mentions: list = field(default_factory=list)
    music: Optional[str] = None
    language: Optional[str] = None
    thumbnail: Optional[str] = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.duration_s = normalize_duration(self.duration_s, self.platform)

    @property
    def uid(self): return f"{self.platform}:{self.video_id}"

@dataclass
class Comment(_Base):
    platform: str
    video_id: str
    comment_id: str
    text: str
    author: Optional[str] = None
    author_id: Optional[str] = None
    created_at: Optional[str] = None
    like_count: Optional[int] = None
    reply_count: Optional[int] = None
    parent_id: Optional[str] = None
    is_author_reply: bool = False
    language: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def uid(self): return f"{self.platform}:{self.comment_id}"

