"""Abrufresultate trennen fehlende Kommentare von einer belegten Null."""
from dataclasses import dataclass, field
from typing import Optional

from .models import Video, Comment


COMMENT_STATUSES = frozenset({"not_requested", "received", "empty", "unavailable", "partial"})


@dataclass
class FetchResult:
    video: Optional[Video]
    comments: list[Comment] = field(default_factory=list)
    comments_status: str = "unavailable"
    message: str = ""

    def __post_init__(self):
        if self.comments_status not in COMMENT_STATUSES:
            raise ValueError("Ungültiger Kommentar-Abrufstatus")

    def __iter__(self):
        """Bestehende Aufrufer können weiter `video, comments = result` verwenden."""
        yield self.video
        yield self.comments


def normalize_result(value, max_comments):
    """Alte Adapter/Fakes erhalten ohne ausdrücklichen Nachweis keinen Null-Status."""
    if isinstance(value, FetchResult):
        return value
    video, comments = value
    comments = list(comments or [])
    if max_comments == 0:
        return FetchResult(video, comments, "not_requested", "Nur Videodaten angefordert.")
    if comments:
        return FetchResult(video, comments, "received")
    if video is not None and getattr(video, "comment_count", None) == 0:
        return FetchResult(video, [], "empty", "Die Plattform meldet keine Kommentare.")
    return FetchResult(video, [], "unavailable",
                       "Kommentare konnten nicht abgerufen werden. Erneut versuchen.")


def comments_failure(video, comments, error):
    """Behält bekannte Daten bei, ohne Kommentarprobleme als Erfolg auszugeben."""
    from .net.errors import explain
    return FetchResult(video, comments, "partial" if comments else "unavailable",
                       "Kommentarabruf unvollständig: " + explain(error))


def received_comments(video, comments, max_comments, end_unconfirmed=False):
    """Ein erkennbar unterschrittener Abrufumfang bleibt ein Teilergebnis."""
    from .util import parse_short_number
    count = parse_short_number(getattr(video, "comment_count", None))
    known_count = count is not None and count >= 0
    target = min(max_comments, count) if known_count else max_comments
    if len(comments) < target and (known_count or end_unconfirmed):
        return FetchResult(video, comments, "partial",
                           f"{len(comments)} Kommentare erhalten; bis zu {target} waren angefordert. "
                           "Weitere Kommentare konnten nicht geladen werden. Später erneut versuchen.")
    return FetchResult(video, comments, "received")
