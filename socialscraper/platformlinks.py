"""Links von Reddit und X erkennen und in eine einheitliche Form bringen (ohne Netz, ohne Browser).

    reddit_post(url)  -> (beitrags_id, subreddit oder None, kanonischer_link) oder None
    x_status(url)     -> (tweet_id, kontoname oder None, kanonischer_link) oder None

Der Host wird echt geprüft (`util.matches_host`): `evil.com/?u=reddit.com` oder `notreddit.com` zählen nicht.
Kurzlinks, die erst im Netz aufgelöst werden müssten (`reddit.com/r/name/s/Code`, `t.co/Code`), werden nur
erkannt (`reddit_share`, `x_short`), ihnen wird hier nie gefolgt.
"""
import re
from urllib.parse import urlsplit

from .util import matches_host

_POST_ID = r'[A-Za-z0-9]{4,10}'
_SUB = r'[A-Za-z0-9_]{2,21}'
_REDDIT_POST = re.compile(
    r'(?:/r/(?P<sub>' + _SUB + r')|/(?:u|user)/[A-Za-z0-9_-]{1,40})?'
    r'/(?:comments|gallery)/(?P<id>' + _POST_ID + r')(?:/[^/]*(?:/[A-Za-z0-9]{3,10})?)?/?')
_REDDIT_SHARE = re.compile(r'/r/' + _SUB + r'/s/[A-Za-z0-9]{4,20}/?')
_REDDIT_SHORT = re.compile(r'/(?P<id>' + _POST_ID + r')/?')
_X_STATUS = re.compile(
    r'/(?:(?P<handle>[A-Za-z0-9_]{1,15})|i(?:/web)?)/status(?:es)?/(?P<id>\d{1,25})(?:/.*)?')
_X_SHORT = re.compile(r'/[A-Za-z0-9]{6,20}/?')

REDDIT_DOMAINS = ('reddit.com', 'redd.it')
X_DOMAINS = ('x.com', 'twitter.com', 't.co')


def _parts(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower().rstrip('.')
    except ValueError:
        return None, ''
    return host, parts.path or ''


def is_reddit(url):
    """Gehört der Link (Host echt geprüft) zu Reddit oder zum Kurzlinkdienst redd.it?"""
    return matches_host(url, *REDDIT_DOMAINS)


def is_x(url):
    """Gehört der Link zu X/Twitter (x.com, twitter.com samt www./mobile.) oder zum Kurzlinkdienst t.co?"""
    host, _ = _parts(url)
    if host in ('t.co',):
        return matches_host(url, 't.co')
    return matches_host(url, 'x.com', 'twitter.com')


def reddit_post(url):
    """(id, subreddit|None, kanonischer Link) eines Reddit-Beitrags, sonst None.

    Erkannt: /r/<sub>/comments/<id>[/<titel>[/<kommentar>]], /comments/<id>, /gallery/<id>,
    /user/<name>/comments/<id>, redd.it/<id>; Hosts reddit.com samt www./old./np./m./new.
    Ein Kommentar-Permalink führt zum ganzen Beitrag."""
    if not isinstance(url, str) or not matches_host(url, *REDDIT_DOMAINS):
        return None
    host, path = _parts(url)
    if host in ('redd.it', 'www.redd.it'):
        match = _REDDIT_SHORT.fullmatch(path)
        if not match:
            return None
        return match.group('id').lower(), None, f'https://www.reddit.com/comments/{match.group("id").lower()}/'
    if host.endswith('.redd.it'):     # i.redd.it, v.redd.it, preview.redd.it: Bild-/Videoserver, kein Beitrag
        return None
    match = _REDDIT_POST.fullmatch(path)
    if not match:
        return None
    post_id, sub = match.group('id').lower(), match.group('sub')
    if sub:
        return post_id, sub, f'https://www.reddit.com/r/{sub}/comments/{post_id}/'
    return post_id, None, f'https://www.reddit.com/comments/{post_id}/'


def reddit_share(url):
    """Neuer Kurzlink https://www.reddit.com/r/<sub>/s/<Code> (erst der Browser löst ihn auf)."""
    if not isinstance(url, str) or not matches_host(url, 'reddit.com'):
        return False
    return bool(_REDDIT_SHARE.fullmatch(_parts(url)[1]))


def x_status(url):
    """(id, Kontoname|None, kanonischer Link) eines Beitrags auf X, sonst None.

    Erkannt: x.com/<name>/status/<id>, twitter.com/…, mobile.twitter.com/…, /i/web/status/<id>, /i/status/<id>,
    auch mit ?s=…&t=… und Zusätzen wie /photo/1. Dienste wie fxtwitter.com zählen nicht."""
    if not isinstance(url, str) or not matches_host(url, 'x.com', 'twitter.com'):
        return None
    match = _X_STATUS.fullmatch(_parts(url)[1])
    if not match:
        return None
    handle = match.group('handle')
    if handle and handle.lower() == 'i':
        handle = None
    tweet_id = match.group('id')
    if handle:
        return tweet_id, handle, f'https://x.com/{handle}/status/{tweet_id}'
    return tweet_id, None, f'https://x.com/i/status/{tweet_id}'


def x_short(url):
    """t.co/<Code>: erkannt, aber nie verfolgt (das Ziel kann irgendwohin führen)."""
    if not isinstance(url, str) or not matches_host(url, 't.co'):
        return False
    return bool(_X_SHORT.fullmatch(_parts(url)[1]))


# ---------- Ziele der Automatik ----------

_SUB_VALUE = re.compile(r'(?:/?r/)(?P<name>' + _SUB + r')/?', re.I)


def reddit_subreddit(value):
    """Name des Subreddits, wenn der Suchwert `r/name` lautet (auch `/r/name/`), sonst None."""
    match = _SUB_VALUE.fullmatch((value or '').strip())
    return match.group('name') if match else None


def reddit_user(value):
    """Benutzername aus `u/name`, `/user/name`, `@name` oder `name`; ungültig = None."""
    text = (value or '').strip().lstrip('@').strip()
    text = re.sub(r'^/?(?:u|user)/', '', text, flags=re.I).strip('/')
    return text if re.fullmatch(r'[A-Za-z0-9_-]{3,20}', text) else None


def x_handle(value):
    """Kontoname ohne @ (1–15 Zeichen: Buchstaben, Zahlen, _), sonst None."""
    text = (value or '').strip().lstrip('@').strip()
    return text if re.fullmatch(r'[A-Za-z0-9_]{1,15}', text) else None
