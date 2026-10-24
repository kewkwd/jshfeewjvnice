"""X (früher Twitter): Beiträge und Antworten.

Mit angemeldetem Konto (Cookies der Identität, sichtbares Anmeldefenster wie bei TikTok):
    Der Beitrag wird unsichtbar im Browser geöffnet. Statt die Seite zu zerlegen, werden die internen Antworten
    der Seite (GraphQL `TweetDetail`, `TweetResultByRestId`) abgefangen und tolerant ausgewertet: Die Struktur ist
    tief verschachtelt und ändert sich, deshalb sucht `iter_tweets` Beitragsobjekte, statt feste Pfade zu
    erwarten. Antworten werden durch Scrollen nachgeladen. Findet sich nichts, liest ein zweiter Weg die
    sichtbaren Beiträge (`article[data-testid="tweet"]`).

Ohne Konto:
    Nur die Daten eines einzelnen Beitrags (ohne Antworten) über den öffentlichen Einbettungsdienst
    `cdn.syndication.twimg.com`. Das Ergebnis sagt klar, dass Antworten eine Anmeldung brauchen.

Hinweis: Die GraphQL-Auswertung ist nur mit selbst gebauten Beispieldaten geprüft, nicht gegen echtes X.
"""
import datetime as dt
import logging
import math
import random
import re
from urllib.parse import quote

from .base import Adapter, checkpoint
from ..fetch_result import FetchResult, comments_failure, received_comments
from ..models import Video, Comment, iso
from ..net import humanize
from ..net.retry import PermanentFetchError, with_retry
from ..platformlinks import x_handle, x_short, x_status
from ..util import comment_limit, parse_short_number

log = logging.getLogger(__name__)

SYNDICATION_URL = 'https://cdn.syndication.twimg.com/tweet-result'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/126.0.0.0 Safari/537.36')
MAX_SCROLLS = 25
GRAPHQL_OPS = ('TweetDetail', 'TweetResultByRestId')
LIST_OPS = ('SearchTimeline', 'UserTweets', 'UserTweetsAndReplies')

MSG_SHORT = ('Kurzlinks von t.co werden nicht verfolgt, weil sie überallhin führen können. Öffne den Link im Browser und '
             'kopiere die Adresse des Beitrags (x.com/…/status/…).')
MSG_NO_POST = 'Das ist kein Link zu einem Beitrag auf X (erwartet: x.com/<name>/status/<Nummer>).'
MSG_NEEDS_LOGIN = 'X braucht für diesen Abruf ein angemeldetes Konto. Melde dich unter „Konten“ an.'
MSG_NO_REPLIES = ('Antworten brauchen eine Anmeldung. Die Angaben zum Beitrag sind gespeichert, aber ohne Antworten. '
                  'Melde dich dafür unter „Konten“ an.')
MSG_UNAVAILABLE = 'Der Beitrag auf X ist nicht abrufbar (gelöscht, geschützt oder gesperrt).'
MSG_RATE = ('X bremst uns aus (zu viele Anfragen). Warte einige Minuten und versuche es danach mit weniger Antworten '
            'erneut.')
MSG_LOCKED = ('X hat das Konto eingeschränkt oder verlangt eine Bestätigung. Öffne x.com in einem normalen Browser, bestätige '
              'dort alles und melde dich danach unter „Konten“ neu an. Die Nutzung eines Zweitkontos ist ratsam.')
MSG_CAPTCHA = 'X zeigt eine Sicherheitsabfrage. Löse sie in einem normalen Browserfenster, melde dich unter „Konten“ neu an und versuche es erneut.'
MSG_NO_DATA = 'X hat keine lesbaren Daten geliefert. Später erneut versuchen.'
MSG_LOST = 'Die X-Anmeldung ist abgelaufen. Melde dich unter „Konten“ erneut an und wiederhole den Abruf.'

LOCK_MARKERS = ('your account has been locked', 'your account is suspended', 'account suspended', 'dein konto wurde gesperrt',
                'konto gesperrt', 'konto ist gesperrt', 'ungewöhnliche aktivität', 'unusual activity', 'wir haben dein konto')
LIMIT_MARKERS = ('rate limit exceeded', 'limit überschritten', 'ratenlimit')
CAPTCHA_MARKERS = ('captcha', 'arkose', 'verify you are human', 'bestätige, dass du ein mensch')
TEXT_JS = "() => (document.body && document.body.innerText || '').slice(0, 2000)"

LOGGED_OUT_JS = """() => {
  const p = location.pathname;
  if (/^\\/(i\\/flow\\/(login|signup)|login|logout)/.test(p)) return true;
  return !!document.querySelector('[data-testid="loginButton"], [data-testid="signupButton"], a[href="/login"], '
                                  + 'a[href="/i/flow/login"], input[autocomplete="username"], input[name="password"]');
}"""

CLICK_MORE_JS = """() => {
  const re = /^(show more replies|weitere antworten anzeigen|show replies|antworten anzeigen)$/i;
  for (const el of document.querySelectorAll('[data-testid="cellInnerDiv"] [role="button"]')) {
    if (re.test((el.innerText || '').trim())) { el.click(); return true; }
  }
  return false;
}"""

# Zweiter Weg: sichtbare Beiträge der Seite.
DOM_TWEETS_JS = """() => {
  const out = [];
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const timeEl = art.querySelector('time');
    const link = timeEl && timeEl.closest('a');
    const m = /^\\/([^\\/]+)\\/status\\/(\\d+)/.exec(link ? (link.getAttribute('href') || '') : '');
    if (!m) continue;
    const textEl = art.querySelector('[data-testid="tweetText"]');
    const nameBox = art.querySelector('[data-testid="User-Name"]');
    const cnt = (id) => { const el = art.querySelector('[data-testid="' + id + '"]'); return el ? (el.innerText || '').trim() : ''; };
    const views = art.querySelector('a[href$="/analytics"]');
    out.push({id: m[2], handle: m[1], name: nameBox ? (nameBox.innerText || '').split('\\n')[0] : '',
              text: textEl ? textEl.innerText : '', time: timeEl.getAttribute('datetime'),
              replies: cnt('reply'), retweets: cnt('retweet'), likes: cnt('like'),
              views: views ? (views.innerText || '').trim() : '',
              media: !!art.querySelector('[data-testid="tweetPhoto"], video')});
  }
  return out;
}"""

LINKS_JS = """() => Array.from(document.querySelectorAll('article[data-testid="tweet"] time'))
  .map(t => { const a = t.closest('a'); return a ? a.getAttribute('href') : null; }).filter(Boolean)"""

_MONTHS = {m: i for i, m in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}


# ---------- Hilfen ----------

def to_iso(value):
    """Zeit aus X: 'Wed Oct 10 20:19:24 +0000 2018' (GraphQL), '2006-03-21T20:50:14.000Z' (Einbettung) oder Zahl."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return iso(value)
    text = str(value).strip()
    match = re.fullmatch(r'[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4}) (\d{4})', text)
    if match:
        month = _MONTHS.get(match.group(1).title())
        if month:
            try:
                zone = dt.timezone(dt.timedelta(hours=int(match.group(6)[:3]), minutes=int(match.group(6)[0] + match.group(6)[3:])))
                return iso(dt.datetime(int(match.group(7)), month, int(match.group(2)), int(match.group(3)),
                                       int(match.group(4)), int(match.group(5)), tzinfo=zone))
            except ValueError:
                return None
    try:
        return iso(dt.datetime.fromisoformat(text.replace('Z', '+00:00')))
    except ValueError:
        return None


def _int(value):
    return parse_short_number(value) if value not in (None, '') else None


def _text(value):
    return value if isinstance(value, str) else None


def syndication_token(tweet_id):
    """Das Kennzeichen der Einbettungsabfrage, aus der Nummer abgeleitet (Zahl / 1e15 * Pi in Basis 36, ohne Nullen
    und Punkt). Live zeigte sich: der Dienst nimmt derzeit jedes nicht leere Kennzeichen an; die Ableitung ist Vorsorge."""
    number = (float(int(tweet_id)) / 1e15) * math.pi
    whole, fraction, digits, out = int(number), number - int(number), '0123456789abcdefghijklmnopqrstuvwxyz', ''
    while whole:
        out, whole = digits[whole % 36] + out, whole // 36
    out = (out or '0') + '.'
    for _ in range(12):
        fraction *= 36
        out += digits[int(fraction)]
        fraction -= int(fraction)
    return out.replace('0', '').replace('.', '') or 'a'


def _d(value):
    return value if isinstance(value, dict) else {}


def _user(tweet):
    """Das Benutzerobjekt eines Beitrags (leer, wenn die Struktur unerwartet aussieht)."""
    user = _d(_d(_d(tweet.get('core')).get('user_results')).get('result'))
    return {} if isinstance(user.get('tweet'), dict) else user      # Hülle statt Benutzer


def _dicts(container, key):
    """Die Einträge einer Liste (z. B. entities['urls']) als dicts; alles Unpassende wird übergangen."""
    items = container.get(key) if isinstance(container, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _clean_text(text, entities, text_range=None, drop_media=True):
    """Beitragstext: führende Antwort-Namen weg (display_text_range), t.co-Adressen durch die echten ersetzt,
    angehängte Bild-/Videoadressen entfernt."""
    if not isinstance(text, str):
        return ''
    if (isinstance(text_range, (list, tuple)) and len(text_range) == 2 and all(isinstance(i, int) for i in text_range)
            and 0 <= text_range[0] < text_range[1] <= len(text)):
        text = text[text_range[0]:text_range[1]]
    for item in _dicts(entities, 'urls'):
        if item.get('url'):
            text = text.replace(item['url'], item.get('expanded_url') or item['url'])
    if drop_media:
        for item in _dicts(entities, 'media'):
            if item.get('url'):
                text = text.replace(item['url'], '')
    return re.sub(r'[ \t]+\n', '\n', text).strip()


# ---------- GraphQL auswerten (tolerant) ----------

def is_tweet(node):
    legacy = node.get('legacy') if isinstance(node, dict) else None
    return isinstance(node, dict) and isinstance(node.get('rest_id'), str) and isinstance(legacy, dict) and 'full_text' in legacy


def iter_tweets(node, _depth=0):
    """Alle Beitragsobjekte in beliebig verschachteltem JSON, in Dokumentreihenfolge. Hüllen wie
    `TweetWithVisibilityResults` werden durchlaufen; in einen Beitrag hinein (zitierte Beiträge) geht es nicht."""
    if _depth > 80:
        return
    if isinstance(node, dict):
        if is_tweet(node):
            yield node
            return
        for value in node.values():
            yield from iter_tweets(value, _depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from iter_tweets(value, _depth + 1)


def find_tombstone(node, _depth=0):
    """Text einer Hinweiskarte („Dieser Beitrag ist nicht verfügbar“) oder '', wenn keine da ist; None = keine Karte."""
    if _depth > 80:
        return None
    if isinstance(node, dict):
        if node.get('__typename') in ('TweetTombstone', 'TweetUnavailable'):
            try:
                return str(node['tombstone']['text']['text'])
            except (KeyError, TypeError):
                return str(node.get('reason') or '')
        for value in node.values():
            found = find_tombstone(value, _depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_tombstone(value, _depth + 1)
            if found is not None:
                return found
    return None


def extract_tweet(tweet):
    """Beitragsobjekt (GraphQL) -> flaches dict mit den Feldern, die wir speichern."""
    legacy = tweet['legacy']
    user = _user(tweet)
    user_legacy, user_core = _d(user.get('legacy')), _d(user.get('core'))
    handle = _text(user_core.get('screen_name')) or _text(user_legacy.get('screen_name'))
    name = _text(user_core.get('name')) or _text(user_legacy.get('name'))
    note = _d(_d(_d(tweet.get('note_tweet')).get('note_tweet_results')).get('result'))
    if isinstance(note.get('text'), str) and note['text'].strip():
        entities = note.get('entity_set') or legacy.get('entities')
        text = _clean_text(note['text'], entities)
    else:
        entities = legacy.get('entities')
        text = _clean_text(legacy.get('full_text'), entities, legacy.get('display_text_range'))
    media = _dicts(legacy.get('extended_entities'), 'media') or _dicts(legacy.get('entities'), 'media')
    duration = None
    for item in media:
        try:
            duration = float(item['video_info']['duration_millis']) / 1000
            break
        except (KeyError, TypeError, ValueError):
            continue
    quoted = retweeted = None
    for key, source in (('quoted_status_result', tweet), ('retweeted_status_result', legacy)):
        inner = next(iter_tweets(source.get(key)), None)
        if inner is not None:
            inner_user = _user(inner)
            info = {'id': inner['rest_id'], 'author': (_text(_d(inner_user.get('core')).get('screen_name'))
                                                       or _text(_d(inner_user.get('legacy')).get('screen_name')))}
            if key == 'quoted_status_result':
                quoted = info
            else:
                retweeted = info
    views = _int(_d(tweet.get('views')).get('count'))
    return {
        'id': tweet['rest_id'], 'text': text, 'created_at': to_iso(legacy.get('created_at')),
        'language': _text(legacy.get('lang')), 'handle': handle, 'name': name, 'author_id': _text(user.get('rest_id')),
        'reply_to': _text(legacy.get('in_reply_to_status_id_str')), 'conversation_id': _text(legacy.get('conversation_id_str')),
        'likes': _int(legacy.get('favorite_count')), 'replies': _int(legacy.get('reply_count')),
        'retweets': _int(legacy.get('retweet_count')), 'quotes': _int(legacy.get('quote_count')),
        'bookmarks': _int(legacy.get('bookmark_count')), 'views': views,
        'hashtags': [h['text'] for h in _dicts(entities, 'hashtags') if h.get('text')],
        'mentions': [m['screen_name'] for m in _dicts(entities, 'user_mentions') if m.get('screen_name')],
        'media_types': list(dict.fromkeys(m.get('type') for m in media if m.get('type'))),
        'thumbnail': next((m.get('media_url_https') for m in media if m.get('media_url_https')), None),
        'duration': duration, 'quoted': quoted, 'retweeted': retweeted,
        'source': 'browser',
    }


def extract_syndication(data):
    """Antwort des Einbettungsdienstes -> dasselbe flache dict."""
    user = data.get('user') or {}
    entities = data.get('entities')
    media = _dicts(data, 'mediaDetails')
    duration = None
    for item in media:
        try:
            duration = float(item['video_info']['duration_millis']) / 1000
            break
        except (KeyError, TypeError, ValueError):
            continue
    quoted = data.get('quoted_tweet') if isinstance(data.get('quoted_tweet'), dict) else None
    return {
        'id': str(data.get('id_str') or ''), 'text': _clean_text(data.get('text'), entities, data.get('display_text_range')),
        'created_at': to_iso(data.get('created_at')), 'language': _text(data.get('lang')),
        'handle': _text(user.get('screen_name')), 'name': _text(user.get('name')), 'author_id': _text(user.get('id_str')),
        'reply_to': _text(data.get('in_reply_to_status_id_str')), 'conversation_id': None,
        'likes': _int(data.get('favorite_count')), 'replies': _int(data.get('conversation_count')),
        'retweets': None, 'quotes': None, 'bookmarks': None, 'views': None,
        'hashtags': [h['text'] for h in _dicts(entities, 'hashtags') if h.get('text')],
        'mentions': [m['screen_name'] for m in _dicts(entities, 'user_mentions') if m.get('screen_name')],
        'media_types': list(dict.fromkeys(m.get('type') for m in media if m.get('type'))),
        'thumbnail': next((m.get('media_url_https') for m in media if m.get('media_url_https')), None),
        'duration': duration,
        'quoted': {'id': str(quoted.get('id_str') or ''), 'author': (quoted.get('user') or {}).get('screen_name')} if quoted else None,
        'retweeted': None, 'source': 'syndication',
    }


def status_url(info):
    return f'https://x.com/{info["handle"]}/status/{info["id"]}' if info.get('handle') else f'https://x.com/i/status/{info["id"]}'


def video_from_info(info, fallback_handle=None):
    """Flaches dict -> Video. share_count = Weiterleitungen + Zitate (die Aufteilung steht in `raw`)."""
    if not info.get('id'):
        raise ValueError('X liefert keine gültige Beitrags-ID')
    handle = info.get('handle') or fallback_handle
    parts = [n for n in (info.get('retweets'), info.get('quotes')) if n is not None]
    raw = {'source': info.get('source'), 'media': info.get('media_types') or [], 'is_reply': bool(info.get('reply_to')),
           'retweet_count': info.get('retweets'), 'quote_count': info.get('quotes'),
           'author_name': info.get('name'), 'conversation_id': info.get('conversation_id'),
           'reply_to': info.get('reply_to'), 'quoted': info.get('quoted'), 'retweeted': info.get('retweeted')}
    return Video.from_dict({
        'platform': 'x', 'video_id': info['id'], 'url': status_url({**info, 'handle': handle}),
        'author': handle, 'author_id': info.get('author_id'), 'caption': info.get('text') or None,
        'created_at': info.get('created_at'), 'duration_s': info.get('duration'),
        'view_count': info.get('views'), 'like_count': info.get('likes'), 'comment_count': info.get('replies'),
        'share_count': sum(parts) if parts else None, 'save_count': info.get('bookmarks'),
        'hashtags': list(info.get('hashtags') or []), 'mentions': list(info.get('mentions') or []),
        'language': info.get('language'), 'thumbnail': info.get('thumbnail'),
        'raw': {k: v for k, v in raw.items() if v not in (None, [], '')} | {'is_reply': raw['is_reply']},
    })


def comment_from_info(info, main):
    """Antwort -> Comment. parent_id ist None bei direkten Antworten auf den Hauptbeitrag."""
    reply_to = info.get('reply_to')
    handle = info.get('handle')
    return Comment.from_dict({
        'platform': 'x', 'video_id': main['id'], 'comment_id': info['id'], 'text': info.get('text') or '',
        'author': handle, 'author_id': info.get('author_id'), 'created_at': info.get('created_at'),
        'like_count': info.get('likes'), 'reply_count': info.get('replies'),
        'parent_id': reply_to if reply_to and reply_to != main['id'] else None,
        'is_author_reply': bool(handle and main.get('handle') and handle.casefold() == main['handle'].casefold()),
        'language': info.get('language'),
        'raw': {k: v for k, v in {'retweet_count': info.get('retweets'), 'quote_count': info.get('quotes'),
                                  'views': info.get('views'), 'source': info.get('source')}.items() if v is not None},
    })


def thread_from_payloads(payloads, tweet_id):
    """TweetDetail-Antworten (in Eingangsreihenfolge) -> (Hauptbeitrag oder None, Antworten).

    Antworten sind Beiträge derselben Unterhaltung, die NACH dem Hauptbeitrag stehen. Beiträge davor (Vorgänger) und
    fremde Empfehlungen (andere Unterhaltung) zählen nicht."""
    ordered, seen = [], set()
    for payload in payloads:
        for tweet in iter_tweets(payload):
            info = extract_tweet(tweet)
            if info['id'] not in seen:
                seen.add(info['id'])
                ordered.append(info)
    main = next((i for i in ordered if i['id'] == tweet_id), None)
    if main is None:
        return None, []
    position = ordered.index(main)
    ancestors = {i['id'] for i in ordered[:position]}
    conversation = main.get('conversation_id') or main['id']
    replies = [i for i in ordered[position + 1:]
               if i.get('conversation_id') in (None, conversation) and i.get('reply_to') not in ancestors
               and (i.get('text') or '').strip()]
    return main, replies


def links_from_payloads(payloads, own_handle=None):
    """Beitragslinks aus Such-/Profil-Antworten. Weiterleitungen und (bei einem Profil) fremde Beiträge fallen weg."""
    links, seen = [], set()
    for payload in payloads:
        for tweet in iter_tweets(payload):
            info = extract_tweet(tweet)
            if info['id'] in seen or info.get('retweeted') or not info.get('handle'):
                continue
            if own_handle and info['handle'].casefold() != own_handle.casefold():
                continue
            seen.add(info['id'])
            links.append(status_url(info))
    return links


def infos_from_payloads(payloads, own_handle=None):
    """Wie links_from_payloads, liefert aber die flachen Angaben (Datum, Länge, Medienarten) für die Warteschlange."""
    infos, seen = [], set()
    for payload in payloads:
        for tweet in iter_tweets(payload):
            info = extract_tweet(tweet)
            if info['id'] in seen or info.get('retweeted') or not info.get('handle'):
                continue
            if own_handle and info['handle'].casefold() != own_handle.casefold():
                continue
            seen.add(info['id'])
            infos.append(info)
    return infos


def info_from_dom(item):
    """Ein sichtbarer Beitrag (DOM_TWEETS_JS) -> flaches dict."""
    return {'id': str(item.get('id') or ''), 'text': (item.get('text') or '').strip(), 'created_at': to_iso(item.get('time')),
            'language': None, 'handle': item.get('handle'), 'name': item.get('name'), 'author_id': None,
            'reply_to': None, 'conversation_id': None, 'likes': _int(item.get('likes')), 'replies': _int(item.get('replies')),
            'retweets': _int(item.get('retweets')), 'quotes': None, 'bookmarks': None, 'views': _int(item.get('views')),
            'hashtags': re.findall(r'#(\w+)', item.get('text') or ''), 'mentions': re.findall(r'@(\w+)', item.get('text') or ''),
            'media_types': ['media'] if item.get('media') else [], 'thumbnail': None, 'duration': None,
            'quoted': None, 'retweeted': None, 'source': 'dom'}


class _Capture:
    """Sammelt die GraphQL-Antworten der Seite. Gelesen wird erst später im Hauptablauf, nicht im Ereignis."""

    def __init__(self, page, ops):
        self.page, self.ops, self.pending = page, tuple(ops), []
        if not callable(getattr(page, 'on', None)):   # Nodriver-Seite: keine Antwort-Ereignisse
            raise PermanentFetchError('X braucht die Browser-Engine Patchright (Einstellungen → Browser-Engine): '
                                      'Mit Nodriver lassen sich die Antworten der Seite nicht mitlesen.')
        page.on('response', self._on)

    def _on(self, response):
        try:
            url = str(response.url)
        except Exception:
            return
        if '/graphql/' in url:
            for op in self.ops:
                if f'/{op}' in url:
                    self.pending.append((op, response))
                    return

    def drain(self):
        """[(Vorgang, JSON)] der seit dem letzten Aufruf eingetroffenen Antworten."""
        items, self.pending = self.pending, []
        out = []
        for op, response in items:
            try:
                out.append((op, response.json()))
            except Exception:
                log.debug('X-Antwort ohne lesbares JSON übersprungen')
        return out

    def close(self):
        try:
            self.page.remove_listener('response', self._on)
        except Exception:
            pass


# ---------- Adapter ----------

class XAdapter(Adapter):
    platform = 'x'
    domains = ('x.com', 'twitter.com', 't.co')
    needs_browser = True
    needs_auth = True

    # -- Anmeldung und Seitenzustand --

    @staticmethod
    def _check_session(page, ctx):
        """Gibt es keine Anmeldung mehr, endet der Abruf mit SessionExpired (Antworten und Suchen brauchen sie)."""
        from ..net.session_guard import SessionExpired, invalidate
        from ..net.notify import notify
        if not ctx.authenticated:
            raise SessionExpired(MSG_NEEDS_LOGIN)
        try:
            logged_out = page.evaluate(LOGGED_OUT_JS)
        except Exception:
            return          # Seite lädt noch; der nächste Versuch entscheidet
        if not logged_out:
            return
        ctx.authenticated = False
        invalidate(ctx.identity, 'x')
        notify('session_lost_runtime', 'X-Session während der Abfrage verloren', identity=ctx.identity, platform='x')
        raise SessionExpired(MSG_LOST)

    @staticmethod
    def problem_message(page):
        """Meldung, wenn die Seite eine Sperre, Begrenzung oder Sicherheitsabfrage zeigt, sonst ''."""
        try:
            low = str(page.evaluate(TEXT_JS) or '').lower()
        except Exception:
            return ''
        if any(m in low for m in LOCK_MARKERS):
            return MSG_LOCKED
        if any(m in low for m in LIMIT_MARKERS):
            return MSG_RATE
        if any(m in low for m in CAPTCHA_MARKERS):
            return MSG_CAPTCHA
        return ''

    # -- Einzelner Beitrag --

    @with_retry(times=3, backoff=3.0)
    def fetch(self, url, ctx):
        cfg = ctx.cfg
        limit = comment_limit(cfg)
        from ..net import proxymode
        proxymode.assert_fetch_allowed(cfg, 'x', limit)      # kostenlose Proxys/Tor: X braucht immer ein Konto
        if x_short(url):
            raise PermanentFetchError(MSG_SHORT)
        ref = x_status(url)
        if ref is None:
            raise ValueError(MSG_NO_POST)
        if not ctx.authenticated:
            if ctx.require_auth or getattr(cfg, 'require_auth', False):
                from ..net.session_guard import SessionExpired
                raise SessionExpired('X benötigt eine bestätigte Anmeldung')
            return self._fetch_public(ref, ctx, limit)
        if ctx.sessions is None:
            raise ValueError('X benötigt eine Browser-Session')
        return self._fetch_browser(ref, ctx, limit)

    def _fetch_public(self, ref, ctx, limit):
        tweet_id, handle, _canonical = ref
        data = self.syndication(tweet_id, ctx)
        if data.get('__typename') in ('TweetTombstone', 'TweetUnavailable') or not data.get('id_str'):
            return FetchResult(None, [], 'unavailable', MSG_UNAVAILABLE)
        video = video_from_info(extract_syndication(data), handle)
        checkpoint(ctx, video, [])
        if not limit:
            return FetchResult(video, [], 'not_requested', 'Nur Videodaten angefordert (ohne Anmeldung, ohne Antworten).')
        return FetchResult(video, [], 'unavailable', MSG_NO_REPLIES)

    @staticmethod
    def syndication(tweet_id, ctx=None):
        """Daten eines einzelnen Beitrags vom öffentlichen Einbettungsdienst (ohne Antworten, ohne Konto)."""
        import requests
        proxies = None
        if ctx is not None and getattr(ctx, 'im', None) is not None:
            proxy = ctx.im.proxy_for(ctx.im.get(ctx.identity, ctx.country, ctx.mobile))
            if proxy:
                from ..net.proxymode import ytdlp_proxy_url
                proxies = {'http': ytdlp_proxy_url(proxy), 'https': ytdlp_proxy_url(proxy)}
        last = None
        for token in dict.fromkeys((syndication_token(tweet_id), 'a')):
            response = requests.get(SYNDICATION_URL, params={'id': tweet_id, 'token': token, 'lang': 'de'},
                                    headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
                                    timeout=20, proxies=proxies)
            status = response.status_code
            if status == 429:
                raise PermanentFetchError(MSG_RATE)
            if status == 404:
                raise PermanentFetchError(MSG_UNAVAILABLE)
            if status == 200:
                try:
                    data = response.json()
                except ValueError:
                    last = status
                    continue
                if isinstance(data, dict):
                    return data
            last = status
        raise PermanentFetchError(MSG_NO_DATA if last in (200, None) else
                                  f'X hat die Abfrage abgelehnt (HTTP {last}). Später erneut versuchen.')

    def _fetch_browser(self, ref, ctx, limit):
        tweet_id, handle, canonical = ref
        with ctx.sessions.acquire(ctx.identity, 'x', ctx.country, ctx.mobile) as page:
            capture = _Capture(page, GRAPHQL_OPS)
            try:
                page.goto(canonical, wait_until='domcontentloaded')
                page.wait_for_timeout(3500)
                self._check_session(page, ctx)
                detail, single, tomb = [], [], None
                main = replies = None
                for attempt in range(4):
                    for op, payload in capture.drain():
                        (detail if op == 'TweetDetail' else single).append(payload)
                        if tomb is None:
                            tomb = find_tombstone(payload)
                    main, replies = thread_from_payloads(detail, tweet_id)
                    if main is None:
                        main, _ = thread_from_payloads(single, tweet_id)
                    if main is not None:
                        break
                    page.wait_for_timeout(1500)
                if main is None:
                    return self._fallback(page, capture, ref, ctx, limit, tomb)
                return self._collect_replies(page, capture, main, replies or [], detail, ctx, limit)
            finally:
                capture.close()

    def _collect_replies(self, page, capture, main, replies, detail, ctx, limit):
        video = video_from_info(main, None)
        checkpoint(ctx, video, [])
        if not limit:
            return FetchResult(video, [], 'not_requested', 'Nur Videodaten angefordert.')
        by_id = {r['id']: r for r in replies}
        comments, stalled, saved = [], 0, 0
        for _ in range(MAX_SCROLLS):
            comments = [comment_from_info(r, main) for r in by_id.values()][:limit]
            if len(comments) > saved:
                saved = len(comments)
                checkpoint(ctx, video, comments)
            if len(comments) >= limit:
                break
            before = len(by_id)
            try:
                self._check_session(page, ctx)
                page.evaluate(CLICK_MORE_JS)
                humanize.scroll(page, 900)
                page.wait_for_timeout(random.randint(900, 1600))
                for op, payload in capture.drain():
                    if op == 'TweetDetail':
                        detail.append(payload)
                _main, new = thread_from_payloads(detail, main['id'])
            except Exception as error:
                if type(error).__name__ == 'SessionExpired':
                    raise
                return comments_failure(video, comments, error)
            for item in new or []:
                by_id.setdefault(item['id'], item)
            stalled = stalled + 1 if len(by_id) == before else 0
            if stalled >= 3:
                break
        comments = [comment_from_info(r, main) for r in by_id.values()][:limit]
        if len(comments) > saved:
            checkpoint(ctx, video, comments)
        log.info('X %s: %s Antworten', main['id'], len(comments))
        if not comments:
            if video.comment_count == 0:
                return FetchResult(video, [], 'empty', 'X meldet keine Antworten.')
            return FetchResult(video, [], 'unavailable', 'X hat keine Antworten geliefert. Das belegt keine leere Liste. '
                               'Anmeldung prüfen und erneut versuchen.')
        return received_comments(video, comments, limit, end_unconfirmed=True)

    def _fallback(self, page, capture, ref, ctx, limit, tomb):
        """Keine Daten aus den internen Antworten: Hinweiskarte melden, sonst die sichtbaren Beiträge lesen."""
        tweet_id, handle, _canonical = ref
        if tomb is not None:
            return FetchResult(None, [], 'unavailable', (tomb.strip() + ' ' if tomb.strip() else '') + MSG_UNAVAILABLE)
        problem = self.problem_message(page)
        if problem:
            raise PermanentFetchError(problem)
        items = []
        try:
            items = [i for i in (page.evaluate(DOM_TWEETS_JS) or []) if isinstance(i, dict)]
        except Exception:
            items = []
        main = next((info_from_dom(i) for i in items if str(i.get('id')) == tweet_id), None)
        if main is None:
            return FetchResult(None, [], 'unavailable', MSG_NO_DATA)
        video = video_from_info(main, handle)
        checkpoint(ctx, video, [])
        if not limit:
            return FetchResult(video, [], 'not_requested', 'Nur Videodaten angefordert.')
        by_id = {}
        stalled = 0
        for _ in range(MAX_SCROLLS):
            for item in items:
                info = info_from_dom(item)
                if info['id'] and info['id'] != tweet_id and info['text']:
                    by_id.setdefault(info['id'], info)
            comments = [comment_from_info(r, main) for r in by_id.values()][:limit]
            checkpoint(ctx, video, comments)
            if len(comments) >= limit:
                break
            before = len(by_id)
            try:
                humanize.scroll(page, 900)
                page.wait_for_timeout(random.randint(900, 1600))
                items = [i for i in (page.evaluate(DOM_TWEETS_JS) or []) if isinstance(i, dict)]
            except Exception as error:
                return comments_failure(video, comments, error)
            stalled = stalled + 1 if len(by_id) == before else 0
            if stalled >= 3:
                break
        comments = [comment_from_info(r, main) for r in by_id.values()][:limit]
        note = 'Gelesen von der sichtbaren Seite (ohne Verschachtelung der Antworten).'
        if not comments:
            return FetchResult(video, [], 'unavailable', 'X hat keine Antworten geliefert. ' + note)
        result = received_comments(video, comments, limit, end_unconfirmed=True)
        result.message = (result.message + ' ' + note).strip()
        return result

    # -- Entdeckung (braucht ein angemeldetes Konto) --

    def _collect_links(self, ctx, page_url, limit, own_handle=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit muss eine nichtnegative ganze Zahl sein')
        if ctx.sessions is None:
            raise ValueError('X benötigt eine Browser-Session')
        if not limit:
            return []
        if not ctx.authenticated:
            from ..net.session_guard import SessionExpired
            raise SessionExpired(MSG_NEEDS_LOGIN)
        links, seen, stalled = [], set(), 0
        with ctx.sessions.acquire(ctx.identity, 'x', ctx.country, ctx.mobile) as page:
            capture = _Capture(page, LIST_OPS)
            try:
                page.goto(page_url, wait_until='domcontentloaded')
                page.wait_for_timeout(4000)
                self._check_session(page, ctx)
                for _ in range(15):
                    before = len(links)
                    payloads = [payload for _op, payload in capture.drain()]
                    found = links_from_payloads(payloads, own_handle)
                    if not found and not links:       # zweiter Weg: sichtbare Beiträge
                        try:
                            found = [self._dom_link(h, own_handle) for h in (page.evaluate(LINKS_JS) or [])]
                        except Exception:
                            found = []
                    for link in found:
                        if link and link not in seen:
                            seen.add(link)
                            links.append(link)
                    if len(links) >= limit:
                        break
                    stalled = stalled + 1 if len(links) == before else 0
                    if stalled >= 3:
                        break
                    humanize.scroll(page, 1200)
                    page.wait_for_timeout(900)
                if not links:
                    problem = self.problem_message(page)
                    if problem:
                        raise PermanentFetchError(problem)
            finally:
                capture.close()
        return links[:limit]

    @staticmethod
    def _dom_link(href, own_handle=None):
        match = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/.*)?', str(href or ''))
        if not match or (own_handle and match.group(1).casefold() != own_handle.casefold()):
            return None
        return f'https://x.com/{match.group(1)}/status/{match.group(2)}'

    MAX_SCROLL_ROUNDS = 400

    def channel_items(self, value, ctx, opts):
        """Beiträge eines Profils: Zeitleiste im Browser mit Konto scrollen und die Antworten der Seite lesen. X zeigt
        eine Zeitleiste nur bis etwa 3.200 Beiträge zurück. Weitergemacht wird durch erneutes Scrollen (bekannte Links zählen nicht doppelt)."""
        from .. import channel_list as cl
        handle = x_handle(value)
        if not handle:
            raise ValueError('Ungültiger X-Kontoname (erlaubt: 1 bis 15 Zeichen aus Buchstaben, Zahlen und _)')
        if ctx.sessions is None:
            raise ValueError('X benötigt eine Browser-Session')
        if not ctx.authenticated:
            from ..net.session_guard import SessionExpired
            raise SessionExpired(MSG_NEEDS_LOGIN)
        collector, result, stalled = cl.Collector(opts), cl.ListResult(), 0
        stop = humanize._remember_stop(opts.stop)
        if stop():
            return result
        with ctx.sessions.acquire(ctx.identity, 'x', ctx.country, ctx.mobile) as page:
            if stop():
                return result
            capture = _Capture(page, LIST_OPS)
            try:
                page.goto(f'https://x.com/{handle}', wait_until='domcontentloaded')
                humanize.wait(4, stop=stop, sleep=lambda seconds: page.wait_for_timeout(seconds * 1000))
                if stop():
                    return result
                self._check_session(page, ctx)
                for _ in range(self.MAX_SCROLL_ROUNDS):
                    if stop():
                        break
                    before = collector.count
                    for info in infos_from_payloads([p for _op, p in capture.drain()], handle):
                        if stop():
                            break
                        types = info.get('media_types') or []
                        if opts.only_video and not any(t in ('video', 'animated_gif') for t in types):
                            continue
                        published = info.get('created_at') or cl.x_time(info['id'])
                        if not collector.add(cl.item(status_url(info), f'x:{info["id"]}', (info.get('text') or '')[:120],
                                                     cl.to_iso(cl.parse_utc(published)) if published else None,
                                                     info.get('duration'), 'x')):
                            break
                    if stop() or collector.done:
                        break
                    if collector.count == before:
                        stalled += 1
                        if stalled >= 4:
                            problem = self.problem_message(page) if not collector.count else ''
                            if stop():
                                break
                            if problem:
                                raise PermanentFetchError(problem)
                            result.exhausted = True
                            break
                    else:
                        stalled = 0
                        collector.flush()
                    if not humanize.scroll(page, 1200, stop=stop):
                        break
                    humanize.wait((900 + int(opts.rng.uniform(0, 1400))) / 1000,
                                  stop=stop, sleep=lambda seconds: page.wait_for_timeout(seconds * 1000))
            finally:
                capture.close()
        collector.flush()
        result.found = collector.count
        result.reached_known = collector.reached_known
        if collector.hit_since:
            result.exhausted = True
        if result.exhausted:
            result.note = 'X zeigt eine Zeitleiste nur bis etwa 3.200 Beiträge zurück.'
        return result

    @with_retry(times=3, backoff=3.0)
    def profile_urls(self, username, ctx, limit=20):
        handle = x_handle(username)
        if not handle:
            raise ValueError('Ungültiger X-Kontoname (erlaubt: 1 bis 15 Zeichen aus Buchstaben, Zahlen und _)')
        return self._collect_links(ctx, f'https://x.com/{handle}', limit, own_handle=handle)

    @with_retry(times=3, backoff=3.0)
    def search_urls(self, query, ctx, limit=20):
        query = (query or '').strip()
        if not query:
            raise ValueError('Suchbegriff fehlt')
        return self._collect_links(ctx, f'https://x.com/search?q={quote(query)}&src=typed_query&f=live', limit)

    @with_retry(times=3, backoff=3.0)
    def hashtag_urls(self, tag, ctx, limit=20):
        tag = str(tag or '').lstrip('#').strip()
        if not re.fullmatch(r'\w{1,100}', tag):
            raise ValueError('Ungültiger X-Hashtag')
        return self._collect_links(ctx, f'https://x.com/search?q={quote("#" + tag)}&src=hashtag_click&f=live', limit)
