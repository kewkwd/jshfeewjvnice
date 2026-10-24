"""Gemeinsame Bausteine, mit denen Adapter die Videoliste eines ganzen Kanals liefern (ohne Netz, ohne Fenster).

Jeder Adapter hat `channel_items(value, ctx, opts) -> ListResult`. Er ruft `opts.emit(...)` für Pakete von Einträgen
auf (der Planer speichert sie sofort in die Warteschlange) und hört auf, wenn `opts.stop()` wahr ist.
Ein Eintrag ist ein Wörterbuch: url, uid, title, published_at (UTC, …Z), duration_s.
"""
import dataclasses
import datetime
import os
import random
import time
from typing import Callable, Optional

from .models import normalize_duration

PAGE = 30                    # nach so vielen Einträgen wird gespeichert und kurz pausiert
TIKTOK_EPOCH_SHIFT = 32      # TikTok-Video-Nummern enthalten die Zeit in den oberen Bits
X_EPOCH_MS = 1288834974657


@dataclasses.dataclass
class ListResult:
    found: int = 0                       # Einträge, die in diesem Aufruf geliefert wurden
    exhausted: bool = False              # die Plattform hat keine weiteren Einträge mehr gezeigt
    reached_known: bool = False          # aufgehört, weil nur noch bekannte Einträge kamen (Beobachten)
    platform_total: Optional[int] = None  # Gesamtzahl laut Plattform, falls lesbar
    note: Optional[str] = None
    cursor: Optional[str] = None         # Stelle zum Weitermachen (Adapter-eigen)
    name: Optional[str] = None           # Anzeigename des Kanals


@dataclasses.dataclass
class ListOptions:
    limit: Optional[int] = None          # höchstens so viele Einträge (None = so viele die Plattform zeigt)
    since: Optional[datetime.datetime] = None   # nur Einträge ab diesem Moment (UTC)
    known: frozenset = frozenset()       # schon bekannte Links (Beobachten: aufhören, wenn nur noch Bekannte kommen)
    stop_after_known: int = 0            # so viele bekannte Einträge in Folge beenden die Liste (0 = nie)
    cursor: Optional[str] = None
    include_shorts: bool = True
    only_video: bool = False
    sort: str = 'new'
    on_items: Optional[Callable] = None  # on_items(items, cursor)
    cancelled: Optional[Callable] = None
    sleep: Callable = time.sleep
    rng: object = random

    def stop(self):
        return bool(self.cancelled and self.cancelled())

    def pause(self, low=0.8, high=2.5):
        """Kurze zufällige Pause zwischen Seiten der Liste (in Tests mit SS_PACE=off aus)."""
        from .net import humanize
        if humanize.pace_enabled():
            humanize.wait(self.rng.uniform(low, high),stop=self.stop,sleep=self.sleep)


class Collector:
    """Sammelt Einträge, wendet Umfang, bekannte Einträge und Limit an und gibt Pakete weiter."""

    def __init__(self, opts):
        self.opts = opts
        self.items, self.buffer, self.seen = [], [], set()
        self.known_run = 0
        self.older_run = 0
        self.done = False            # Umfang erreicht (Limit, Datum, nur noch Bekannte)
        self.reached_known = False
        self.hit_limit = False
        self.hit_since = False

    @property
    def count(self):
        return len(self.items)

    def add(self, item, cursor=None):
        """Nimmt einen Eintrag auf. Gibt False zurück, wenn die Liste hier enden soll."""
        if self.done:
            return False
        url = item['url']
        if url in self.seen:
            return True
        self.seen.add(url)
        opts = self.opts
        published = parse_utc(item.get('published_at'))
        if opts.since is not None and published is not None and published < opts.since - datetime.timedelta(days=2):
            self.older_run += 1
            if self.older_run >= 3:     # angeheftete Beiträge stehen oben: erst der dritte alte in Folge beendet die Liste
                self.done = self.hit_since = True
                return False
            return True
        self.older_run = 0
        if opts.stop_after_known:
            if url in opts.known:
                self.known_run += 1
                if self.known_run >= opts.stop_after_known:
                    self.done = self.reached_known = True
                    return False
                return True
            self.known_run = 0
        if opts.limit is not None and self.count >= opts.limit:
            self.done = self.hit_limit = True
            return False
        self.items.append(item)
        self.buffer.append(item)
        if len(self.buffer) >= PAGE:
            self.flush(cursor)
        if opts.limit is not None and self.count >= opts.limit:
            self.done = self.hit_limit = True
            return False
        return True

    def flush(self, cursor=None):
        if self.buffer and self.opts.on_items:
            batch, self.buffer = self.buffer, []
            self.opts.on_items(batch, cursor)
        else:
            self.buffer = []


def parse_utc(text):
    if not text:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc).astimezone(datetime.timezone.utc)


def to_iso(moment):
    """datetime (oder Unix-Sekunden) -> 2026-10-12T08:15:00Z, sonst None."""
    if moment is None:
        return None
    try:
        if not isinstance(moment, datetime.datetime):
            moment = datetime.datetime.fromtimestamp(float(moment), datetime.timezone.utc)
        return moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc).astimezone(
            datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def tiktok_time(video_id):
    """Zeitpunkt (UTC-Text) aus der TikTok-Video-Nummer (die oberen Bits sind die Unix-Sekunden)."""
    try:
        seconds = int(video_id) >> TIKTOK_EPOCH_SHIFT
        return to_iso(seconds) if 1_400_000_000 < seconds < 4_000_000_000 else None
    except (TypeError, ValueError):
        return None


def x_time(tweet_id):
    try:
        ms = (int(tweet_id) >> 22) + X_EPOCH_MS
        return to_iso(ms / 1000.0) if ms > 1_300_000_000_000 else None
    except (TypeError, ValueError):
        return None


def item(url, uid=None, title=None, published_at=None, duration=None, platform=None):
    return {'url': url, 'uid': uid, 'title': (title or None), 'published_at': published_at,
            'duration_s': normalize_duration(duration, platform)}
