"""Entdeckung: aus einem Ziel (Hashtag, Suche, Profil) eine Liste neuer Video-Links machen.

    urls = discover('hashtag', 'youtube', 'lofi', ctx, limit=20)

Die Links sind kanonisch (ohne Verfolgungsparameter), ohne Doppelte und auf `limit` begrenzt.
Es werden nur Metadaten gelesen, nichts heruntergeladen. Instagram wird nicht unterstützt.
Reddit: Suche (auch `r/name` = neue Beiträge eines Subreddits) und Profil (`u/name`), kein Hashtag.
X: Hashtag, Suche und Profil, alle nur mit angemeldetem Konto.
"""
import re
from urllib.parse import parse_qs, urlsplit

from .adapters import ALL
from .net import proxymode
from .platformlinks import reddit_post, x_status
from .util import matches_host


class DiscoveryError(ValueError):
    """Entdeckung nicht möglich (nicht unterstützte Kombination)."""


def canonical_url(url):
    """Einheitliche Form eines Video-Links; `None`, wenn es keiner ist."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if matches_host(url, 'youtube.com'):
        if parsed.path == '/watch':
            video_id = (parse_qs(parsed.query).get('v') or [''])[0]
        else:
            match = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{6,20})/?', parsed.path)
            video_id = match.group(1) if match else ''
        return f'https://www.youtube.com/watch?v={video_id}' if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id) else None
    if matches_host(url, 'youtu.be'):
        video_id = parsed.path.strip('/')
        return f'https://www.youtube.com/watch?v={video_id}' if re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id) else None
    if matches_host(url, 'tiktok.com'):
        match = re.fullmatch(r'/(@[^/]+)/video/(\d+)/?', parsed.path)
        return f'https://www.tiktok.com/{match.group(1)}/video/{match.group(2)}' if match else None
    if matches_host(url, 'reddit.com', 'redd.it'):
        ref = reddit_post(url)
        return ref[2] if ref else None
    if matches_host(url, 'x.com', 'twitter.com'):
        ref = x_status(url)
        return ref[2] if ref else None
    return None


def normalize(urls, limit):
    out, seen = [], set()
    for url in urls or []:
        if len(out) >= limit:   # vor dem Anhängen prüfen, sonst kommt bei limit 0 ein Link durch
            break
        canonical = canonical_url(url)
        if canonical and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def _adapter(platform):
    for adapter in ALL:
        if adapter.platform == platform:
            return adapter
    raise DiscoveryError('Unbekannte Plattform')


def needs_login(kind, platform):
    """Brauchen wir für diese Entdeckung ein angemeldetes Konto? (TikTok-Hashtags/-Suche ja, X immer.)"""
    return platform == 'x' or platform == 'instagram' and kind == 'channel' or (
        platform == 'tiktok' and kind in ('hashtag', 'search', 'channel'))


def list_channel(platform, value, ctx, opts):
    """Videoliste eines ganzen Kanals (siehe channel_list.py / channels.py). Gibt ein ListResult zurück."""
    adapter = _adapter(platform)
    if not hasattr(adapter, 'channel_items'):
        raise DiscoveryError('Für diese Plattform lassen sich keine ganzen Kanäle abrufen.')
    proxymode.assert_fetch_allowed(getattr(ctx, 'cfg', None), platform, 0, 'profile')
    return adapter.channel_items(value, ctx, opts)


def discover(kind, platform, value, ctx, limit=20):
    """Gibt eine Liste kanonischer Video-Links zurück (höchstens `limit`)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('limit muss eine nichtnegative ganze Zahl sein')
    if platform == 'instagram':
        raise DiscoveryError('Bei Instagram kann Socialscraper keine Videos suchen. '
                             'Bitte einzelne Video-Links als Ziel eintragen.')
    if kind not in ('hashtag', 'search', 'profile'):
        raise DiscoveryError('Nur Hashtags, Suchbegriffe und Profile lassen sich durchsuchen')
    adapter = _adapter(platform)
    # Kostenlose Proxys/Tor: Entdeckung nur ohne Konto (TikTok-Hashtags/-Suche brauchen eines).
    proxymode.assert_fetch_allowed(getattr(ctx, 'cfg', None), platform, 0, kind)
    if platform == 'youtube':
        if kind == 'profile':
            raise DiscoveryError('Bei YouTube lassen sich keine Profile verfolgen')
        value = str(value or '').strip()
        query = '#' + value.lstrip('#') if kind == 'hashtag' else value
        found = adapter.search_urls(query, ctx, limit)
    elif platform == 'tiktok':
        if kind == 'hashtag':
            found = adapter.hashtag_urls(value, ctx, limit)
        elif kind == 'search':
            found = adapter.search_urls(value, ctx, limit)
        else:
            found = adapter.profile_urls(value, ctx, limit)
    elif platform == 'reddit':
        if kind == 'hashtag':
            raise DiscoveryError('Bei Reddit gibt es keine Hashtags. Nutze eine Suche oder ein Subreddit (r/name).')
        found = adapter.search_urls(value, ctx, limit) if kind == 'search' else adapter.profile_urls(value, ctx, limit)
    elif platform == 'x':
        if kind == 'hashtag':
            found = adapter.hashtag_urls(value, ctx, limit)
        elif kind == 'search':
            found = adapter.search_urls(value, ctx, limit)
        else:
            found = adapter.profile_urls(value, ctx, limit)
    else:
        raise DiscoveryError('Unbekannte Plattform')
    return normalize(found, limit)
