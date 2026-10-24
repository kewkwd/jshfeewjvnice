"""Reddit: Beiträge und Kommentare, nur über den Browser (kein API-Schlüssel, Anmeldung freiwillig).

Ablauf: Der Beitrag wird unsichtbar im Browser geöffnet; die Daten holt die Seite selbst über den JSON-Weg
desselben Ursprungs (`/r/<sub>/comments/<id>/.json`). Die Anfrage kommt damit vom echten Browser. Weitere Kommentare
(„more“-Objekte) lädt `/api/morechildren.json`. Zeigt Reddit die Seite, aber der JSON-Weg liefert nichts Brauchbares,
wird die gerenderte Seite gelesen (`shreddit-post`, `shreddit-comment`).

Wichtig: Reddit sperrt manche Internetverbindungen ganz („blocked by network security“). Das erkennt der Adapter und
meldet es klar; er versucht nicht, die Sperre zu umgehen.
"""
import json
import logging
import random
from urllib.parse import quote

from .base import Adapter, checkpoint
from ..fetch_result import FetchResult, comments_failure
from ..models import Video, Comment, iso
from ..net.errors import MESSAGES
from ..net.retry import PermanentFetchError, with_retry
from ..platformlinks import reddit_post, reddit_share, reddit_subreddit, reddit_user
from ..util import comment_limit

log = logging.getLogger(__name__)

HOME = 'https://www.reddit.com'
REMOVED = ('[deleted]', '[removed]')
MAX_PER_CALL = 100            # so viele Kommentarnummern nimmt /api/morechildren.json auf einmal
MAX_ROUNDS = 40               # Sicherung gegen Endlosschleifen beim Nachladen

BLOCK_MARKERS = ('blocked by network security', "you've been blocked", 'you’ve been blocked',
                 'whoa there, pardner', 'your request has been blocked', 'verbindung wurde blockiert')
CAPTCHA_MARKERS = ('prove your humanity',)       # nur die eindeutige Überschrift der Reddit-Prüfseite, nie ein Wort wie „captcha“

MSG_BLOCKED = MESSAGES['sperre']
MSG_RATE = ('Reddit bremst uns aus (zu viele Anfragen). Warte einige Minuten und versuche es danach mit weniger '
            'Kommentaren oder größerem Abstand zwischen den Abrufen erneut.')
MSG_CAPTCHA = ('Reddit verlangt einen Menschen-Test („Prove your humanity“) und zeigt die Beiträge nicht. Ohne Anmeldung '
               'passiert das bei Beiträgen mit Kommentaren oft. Melde dich unter „Konten“ mit einem Reddit-Konto an und '
               'versuche es erneut (ob das reicht, entscheidet Reddit), oder probiere später ein anderes Netz.')
MSG_NOT_FOUND = 'Der Reddit-Beitrag wurde nicht gefunden. Er ist gelöscht oder der Link stimmt nicht.'
MSG_PRIVATE = ('Dieses Subreddit ist privat, gesperrt oder nur mit Anmeldung sichtbar. Melde dich unter „Konten“ mit einem '
               'Reddit-Konto an, das Zugriff hat, und versuche es erneut.')
MSG_NO_JSON = 'Reddit hat keine lesbaren Daten geliefert. Später erneut versuchen.'
MSG_NO_POST = 'Das ist kein Link zu einem Reddit-Beitrag (erwartet: reddit.com/r/<name>/comments/<id>/ oder redd.it/<id>).'

# Holt einen Pfad desselben Ursprungs im Seitenkontext (mit den Cookies der Sitzung, falls angemeldet).
FETCH_JS = """
async ([path]) => {
  try {
    const r = await fetch(path, {credentials: 'include', headers: {'Accept': 'application/json'}});
    const text = await r.text();
    return {status: r.status, retry: r.headers.get('retry-after'), text: text.slice(0, 8000000)};
  } catch (e) { return {error: String(e)}; }
}
"""

TEXT_JS = "() => (document.body && document.body.innerText || '').slice(0, 1500)"

# Zweiter Weg, wenn der JSON-Weg nichts liefert: die gerenderte Seite (Shreddit-Webkomponenten).
DOM_JS = """
() => {
  const post = document.querySelector('shreddit-post');
  if (!post) return null;
  const a = (el, n) => el.getAttribute(n);
  const out = {post: {
    id: (a(post, 'id') || '').replace(/^t3_/, ''), title: a(post, 'post-title'),
    author: a(post, 'author'), score: a(post, 'score'), comments: a(post, 'comment-count'),
    created: a(post, 'created-timestamp'), permalink: a(post, 'permalink'),
    subreddit: a(post, 'subreddit-name') || a(post, 'subreddit-prefixed-name'),
    text: ((post.querySelector('[slot="text-body"]') || {}).innerText || ''),
    nsfw: a(post, 'nsfw') !== null, content_href: a(post, 'content-href')
  }, comments: []};
  for (const c of document.querySelectorAll('shreddit-comment')) {
    const body = c.querySelector('[slot="comment"]');
    const time = c.querySelector('time');
    out.comments.push({
      id: (a(c, 'thingid') || '').replace(/^t1_/, ''), parent: (a(c, 'parentid') || '').replace(/^t1_/, ''),
      author: a(c, 'author'), score: a(c, 'score'), depth: a(c, 'depth'),
      created: time ? time.getAttribute('datetime') : null, text: body ? body.innerText : ''
    });
  }
  return out;
}
"""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean_name(name):
    """Autorname; gelöschte Konten ([deleted]) zählen nicht als Autor."""
    name = (name or '').strip() if isinstance(name, str) else ''
    return None if not name or name in REMOVED else name


def is_placeholder(body):
    """Ein gelöschter oder entfernter Kommentar hat keinen eigenen Text, nur den Platzhalter."""
    return (body or '').strip() in REMOVED


# ---------- Auswerten (reine Funktionen, ohne Browser) ----------

def reddit_duration(data):
    """Länge eines Reddit-Videobeitrags in Sekunden (`secure_media.reddit_video.duration`, sonst `media…`, auch beim
    Original eines Crossposts) oder None."""
    sources = [data] + [p for p in (data.get('crosspost_parent_list') or []) if isinstance(p, dict)][:1]
    for source in sources:
        for key in ('secure_media', 'media'):
            media = source.get(key)
            video = media.get('reddit_video') if isinstance(media, dict) else None
            if isinstance(video, dict) and video.get('duration') is not None:
                return video['duration']
    return None


def is_video_post(data):
    return bool(data.get('is_video') or reddit_duration(data) is not None
                or str(data.get('post_hint') or '') in ('hosted:video', 'rich:video'))


def video_from_post(data):
    """Video aus dem `data`-Block eines Beitrags (t3)."""
    post_id = str(data.get('id') or '').strip()
    if not post_id:
        raise ValueError('Reddit liefert keine gültige Beitrags-ID')
    title = (data.get('title') or '').strip()
    body = (data.get('selftext') or '').strip()
    if body in REMOVED:
        body = ''
    caption = title + ('\n\n' + body if body else '')
    permalink = data.get('permalink') or ''
    url = HOME + permalink if permalink.startswith('/') else f'{HOME}/comments/{post_id}/'
    raw = {'subreddit': data.get('subreddit'), 'upvote_ratio': data.get('upvote_ratio'),
           'flair': data.get('link_flair_text'), 'is_self': bool(data.get('is_self')),
           'over_18': bool(data.get('over_18')), 'url_overridden_by_dest': data.get('url_overridden_by_dest'),
           'author_fullname': data.get('author_fullname'), 'stickied': bool(data.get('stickied')),
           'locked': bool(data.get('locked')), 'num_crossposts': data.get('num_crossposts')}
    parents = data.get('crosspost_parent_list')
    if isinstance(parents, list) and parents and isinstance(parents[0], dict):
        first = parents[0]
        raw['crosspost'] = {'id': first.get('id'), 'subreddit': first.get('subreddit'), 'author': first.get('author'),
                            'permalink': first.get('permalink')}
    elif data.get('crosspost_parent'):
        raw['crosspost'] = {'id': str(data['crosspost_parent']).removeprefix('t3_')}
    thumb = data.get('thumbnail')
    thumbnail = thumb if isinstance(thumb, str) and thumb.startswith('http') else None
    try:
        thumbnail = data['preview']['images'][0]['source']['url'] or thumbnail
    except (KeyError, IndexError, TypeError):
        pass
    return Video.from_dict({
        'platform': 'reddit', 'video_id': post_id, 'url': url, 'author': clean_name(data.get('author')),
        'caption': caption or None, 'created_at': iso(data.get('created_utc')), 'duration_s': reddit_duration(data),
        'view_count': None, 'like_count': _int(data.get('score')), 'comment_count': _int(data.get('num_comments')),
        'share_count': None, 'hashtags': [], 'music': None, 'thumbnail': thumbnail,
        'raw': {k: v for k, v in raw.items() if v is not None and v != ''},
    })


def comment_from_data(data, post_id, post_author):
    """Comment aus dem `data`-Block eines Kommentars (t1) oder None bei Platzhaltern ohne eigenen Text."""
    cid = str(data.get('id') or '').strip()
    body = (data.get('body') or '').strip()
    if not cid or not body or is_placeholder(body):
        return None
    parent = str(data.get('parent_id') or '')
    author = clean_name(data.get('author'))
    return Comment.from_dict({
        'platform': 'reddit', 'video_id': post_id, 'comment_id': cid, 'text': body, 'author': author,
        'created_at': iso(data.get('created_utc')), 'like_count': _int(data.get('score')),
        'parent_id': parent[3:] if parent.startswith('t1_') else None,
        'is_author_reply': bool(author and post_author and author.casefold() == post_author.casefold()),
        'raw': {k: v for k, v in {'distinguished': data.get('distinguished'), 'stickied': data.get('stickied') or None,
                                  'depth': data.get('depth'), 'edited': data.get('edited') or None}.items()
                if v is not None},
    })


class Thread:
    """Der Inhalt eines Beitrags: Beitrag, Kommentare in Anzeigereihenfolge und was noch nachzuladen ist."""

    def __init__(self, post_id, post_author=None):
        self.post_id, self.post_author = post_id, post_author
        self.comments, self.seen = [], set()
        self.more = []                  # ('children', [ids], parent) oder ('thread', kommentar_id)
        self.removed = 0                # übersprungene gelöschte/entfernte Kommentare
        self.error = None               # Fehler beim Nachladen (die schon erhaltenen Kommentare bleiben)

    def add_listing(self, listing, limit=None):
        """Nimmt eine Kommentarliste (kind=Listing) auf; stoppt bei `limit` Kommentaren."""
        for child in ((listing or {}).get('data') or {}).get('children') or []:
            if limit is not None and len(self.comments) >= limit:
                return
            self.add_thing(child, limit)

    def add_thing(self, thing, limit=None):
        if not isinstance(thing, dict):
            return
        kind, data = thing.get('kind'), thing.get('data') or {}
        if kind == 'more':
            children = [str(c) for c in data.get('children') or [] if c]
            if children:
                self.more.append(('children', children, str(data.get('parent_id') or '')))
            elif str(data.get('parent_id') or '').startswith('t1_'):
                self.more.append(('thread', str(data['parent_id'])[3:]))
            return
        if kind != 't1':
            return
        cid = str(data.get('id') or '')
        if cid and cid not in self.seen:
            self.seen.add(cid)
            comment = comment_from_data(data, self.post_id, self.post_author)
            if comment is None:
                self.removed += 1
            elif limit is None or len(self.comments) < limit:
                self.comments.append(comment)
        replies = data.get('replies')
        if isinstance(replies, dict):
            self.add_listing(replies, limit)


def parse_thread_json(payload):
    """([Beitragsliste, Kommentarliste]) -> (post_data, kommentarliste)."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError('Reddit liefert keine gültige Beitragsliste')
    children = ((payload[0] or {}).get('data') or {}).get('children') or []
    post = next((c.get('data') for c in children if isinstance(c, dict) and c.get('kind') == 't3'), None)
    if not isinstance(post, dict):
        raise ValueError('Reddit liefert keinen Beitrag')
    return post, payload[1]


def morechildren_things(payload):
    """/api/morechildren.json -> Liste der Dinge (t1 und more)."""
    try:
        errors = payload['json'].get('errors') or []
        things = payload['json']['data']['things']
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Reddit liefert keine gültige Antwort beim Nachladen') from None
    if errors:
        raise ValueError('Reddit hat das Nachladen abgelehnt')
    return things if isinstance(things, list) else []


def listing_links(payload):
    """Beitragsliste (z. B. /r/x/new.json) -> (Links, nächste Seite oder None)."""
    data = (payload or {}).get('data') or {}
    links = []
    for child in data.get('children') or []:
        post = (child or {}).get('data') or {}
        if child.get('kind') == 't3' and str(post.get('permalink') or '').startswith('/'):
            links.append(HOME + post['permalink'])
    after = data.get('after')
    return links, (after if isinstance(after, str) and after else None)


def listing_entries(payload):
    """Beitragsliste -> (Einträge für die Warteschlange, nächste Seite oder None). Ein Eintrag hat url, uid, title,
    published_at, duration_s und is_video."""
    from ..channel_list import item, to_iso
    data = (payload or {}).get('data') or {}
    entries = []
    for child in data.get('children') or []:
        post = (child or {}).get('data') or {}
        permalink = str(post.get('permalink') or '')
        if child.get('kind') == 't3' and permalink.startswith('/') and post.get('id'):
            entry = item(HOME + permalink, f'reddit:{str(post["id"]).lower()}', post.get('title'),
                         to_iso(post.get('created_utc')), reddit_duration(post), 'reddit')
            entry['is_video'] = is_video_post(post)
            entries.append(entry)
    after = data.get('after')
    return entries, (after if isinstance(after, str) and after else None)


def thread_from_dom(dom, post_id=None):
    """Zweiter Weg: Ergebnis von DOM_JS -> (Video, Thread). Zahlen können Text wie „1,2 Tsd.“ sein."""
    from ..util import parse_short_number
    post = dom.get('post') or {}
    pid = post_id or str(post.get('id') or '')
    if not pid:
        raise ValueError('Reddit liefert keine gültige Beitrags-ID')
    permalink = post.get('permalink') or ''
    data = {'id': pid, 'title': post.get('title'), 'selftext': post.get('text'), 'author': post.get('author'),
            'score': parse_short_number(post.get('score')), 'num_comments': parse_short_number(post.get('comments')),
            'permalink': permalink if permalink.startswith('/') else '',
            'subreddit': str(post.get('subreddit') or '').removeprefix('r/') or None,
            'over_18': bool(post.get('nsfw')), 'url_overridden_by_dest': post.get('content_href'),
            'created_utc': _parse_time(post.get('created')), 'is_self': not post.get('content_href')}
    video = video_from_post(data)
    thread = Thread(pid, video.author)
    for item in dom.get('comments') or []:
        cid = str(item.get('id') or '')
        if not cid or cid in thread.seen:
            continue
        thread.seen.add(cid)
        data = {'id': cid, 'body': item.get('text'), 'author': item.get('author'),
                'score': parse_short_number(item.get('score')), 'created_utc': _parse_time(item.get('created')),
                'parent_id': ('t1_' + item['parent']) if item.get('parent') else 't3_' + pid,
                'depth': _int(item.get('depth'))}
        comment = comment_from_data(data, pid, video.author)
        if comment is None:
            thread.removed += 1
        else:
            thread.comments.append(comment)
    return video, thread


def _parse_time(text):
    import datetime as dt
    if not isinstance(text, str) or not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


# ---------- Adapter ----------

class RedditAdapter(Adapter):
    platform = 'reddit'
    domains = ('reddit.com', 'redd.it')
    needs_browser = True
    needs_auth = False          # Anmeldung ist freiwillig; Cookies eines angemeldeten Kontos werden trotzdem benutzt

    # -- Browser-Hilfen --

    @staticmethod
    def blocked_message(page):
        """Deutsche Meldung, wenn die geöffnete Seite eine Sperr-, Captcha- oder Fehlerseite ist, sonst ''."""
        try:
            text = str(page.evaluate(TEXT_JS) or '').lower()
        except Exception:
            return ''
        return RedditAdapter.message_for_text(text)

    @staticmethod
    def message_for_text(text):
        low = (text or '').lower()
        if any(marker in low for marker in BLOCK_MARKERS):
            return MSG_BLOCKED
        if any(marker in low for marker in CAPTCHA_MARKERS):
            return MSG_CAPTCHA
        return ''

    @staticmethod
    def fetch_json(page, path):
        """JSON im Seitenkontext holen. Gibt die ausgewertete Antwort zurück; Fehler sind deutsche Ausnahmen."""
        result = page.evaluate(FETCH_JS, [path]) or {}
        if result.get('error'):
            raise RuntimeError('Reddit-Abfrage im Browser fehlgeschlagen (Verbindung)')
        status, text = result.get('status'), result.get('text') or ''
        blocked = RedditAdapter.message_for_text(text[:400000]) if status != 200 or text.lstrip()[:1] not in '[{' else ''
        if blocked:
            raise PermanentFetchError(blocked)
        if status == 429:
            raise PermanentFetchError(MSG_RATE)
        if status == 404:
            raise PermanentFetchError(MSG_NOT_FOUND)
        if status in (401, 403):
            try:
                reason = str(json.loads(text).get('reason') or '').lower()
            except (ValueError, AttributeError):
                reason = ''
            if reason in ('private', 'banned', 'quarantined', 'gated', 'user_required'):
                raise PermanentFetchError(MSG_PRIVATE)
            raise PermanentFetchError('Reddit verweigert den Zugriff (Status 403). Eine Anmeldung oder ein anderes Netz '
                                      'kann helfen; später noch einmal versuchen.')
        if status != 200:
            raise RuntimeError(f'Reddit-Antwort mit Status {status}')
        try:
            return json.loads(text)
        except ValueError:
            raise ValueError(MSG_NO_JSON) from None

    @staticmethod
    def _pause(page, low=1200, high=2800):
        page.wait_for_timeout(random.randint(low, high))

    # -- Beitrag --

    @with_retry(times=3, backoff=2.0)
    def fetch(self, url, ctx):
        cfg = ctx.cfg
        limit = comment_limit(cfg)
        from ..net import proxymode
        proxymode.assert_fetch_allowed(cfg, 'reddit', limit)
        ref = reddit_post(url)
        if ref is None and not reddit_share(url):
            raise ValueError(MSG_NO_POST)
        if ctx.sessions is None:
            raise ValueError('Reddit benötigt eine Browser-Session')
        with ctx.sessions.acquire(ctx.identity, 'reddit', ctx.country, ctx.mobile) as page:
            page.goto(ref[2] if ref else url, wait_until='domcontentloaded')
            page.wait_for_timeout(2500)
            message = self.blocked_message(page)
            if message:
                raise PermanentFetchError(message)
            if ref is None:      # Kurzlink: der Browser hat ihn aufgelöst, jetzt steht die echte Adresse da
                ref = reddit_post(getattr(page, 'url', '') or '')
                if ref is None:
                    raise PermanentFetchError(MSG_NO_POST)
            post_id, sub, _canonical = ref
            base = f'/r/{sub}/comments/{post_id}/' if sub else f'/comments/{post_id}/'
            fetch_limit = min(500, max(1, limit))
            try:
                payload = self.fetch_json(page, f'{base}.json?limit={fetch_limit}&sort=top&raw_json=1')
                post, listing = parse_thread_json(payload)
                video = video_from_post(post)
                thread = Thread(post_id, video.author)
                thread.add_listing(listing, limit)
                dom_mode = False
            except ValueError:
                # Der JSON-Weg gab nichts Brauchbares: die gerenderte Seite lesen (nicht gegen echtes Reddit geprüft).
                dom = page.evaluate(DOM_JS)
                if not dom:
                    return FetchResult(None, [], 'unavailable', self.blocked_message(page) or MSG_NO_JSON)
                video, thread = thread_from_dom(dom, post_id)
                dom_mode = True
            checkpoint(ctx, video, [])
            if not limit:
                return FetchResult(video, [], 'not_requested', 'Nur Videodaten angefordert.')
            if not dom_mode:
                self._load_more(page, thread, post_id, sub, limit, ctx, video)
            return self._result(video, thread, limit, thread.error, dom_mode, ctx)

    def _load_more(self, page, thread, post_id, sub, limit, ctx, video):
        saved = len(thread.comments)
        if saved:
            checkpoint(ctx, video, thread.comments)
        rounds = 0
        while thread.more and len(thread.comments) < limit and rounds < MAX_ROUNDS:
            rounds += 1
            item = thread.more.pop(0)
            try:    # nur Netz und Auswertung; Fehler beim Speichern (checkpoint) laufen weiter nach oben
                self._pause(page)
                self._load_item(page, thread, item, post_id, sub, limit)
            except Exception as exc:
                thread.error = exc
                break
            if len(thread.comments) > saved:
                saved = len(thread.comments)
                checkpoint(ctx, video, thread.comments)

    def _load_item(self, page, thread, item, post_id, sub, limit):
        if item[0] == 'children':
            ids = item[1][:MAX_PER_CALL]
            if len(item[1]) > MAX_PER_CALL:
                thread.more.insert(0, ('children', item[1][MAX_PER_CALL:], item[2]))
            path = (f'/api/morechildren.json?api_type=json&link_id=t3_{post_id}&sort=top&raw_json=1'
                    f'&children={quote(",".join(ids), safe=",")}')
            for thing in morechildren_things(self.fetch_json(page, path)):
                thread.add_thing(thing, limit)
        else:
            base = f'/r/{sub}/comments/{post_id}/_/' if sub else f'/comments/{post_id}/_/'
            payload = self.fetch_json(page, f'{base}{item[1]}.json?limit={min(500, limit)}&sort=top&raw_json=1')
            _post, listing = parse_thread_json(payload)
            thread.add_listing(listing, limit)

    def _result(self, video, thread, limit, error, dom_mode, ctx):
        comments = thread.comments[:limit]
        if comments:
            checkpoint(ctx, video, comments)
        notes = []
        if thread.removed:
            notes.append(f'{thread.removed} gelöschte oder entfernte Kommentare übersprungen.')
        if dom_mode:
            notes.append('Gelesen von der sichtbaren Seite (nur die dort geladenen Kommentare).')
        note = ' '.join(notes)
        if error is not None:
            result = comments_failure(video, comments, error)
            result.message = (result.message + ' ' + note).strip()
            return result
        complete = len(comments) >= limit or (not thread.more and not dom_mode)
        if not comments:
            if complete and (video.comment_count == 0 or thread.removed):
                return FetchResult(video, [], 'empty', ('Reddit meldet keine Kommentare mit Text. ' + note).strip())
            return FetchResult(video, [], 'unavailable', ('Reddit hat keine Kommentare geliefert. Das belegt keine leere '
                                                          'Kommentarliste. Später erneut versuchen. ' + note).strip())
        if complete:
            return FetchResult(video, comments, 'received', note)
        return FetchResult(video, comments, 'partial',
                           (f'{len(comments)} Kommentare erhalten; bis zu {limit} waren angefordert. '
                            'Weitere Kommentare konnten nicht geladen werden. Später erneut versuchen. ' + note).strip())

    # -- Entdeckung: Listen neuer Beiträge --

    def _listing(self, ctx, page_url, json_path, limit):
        """Öffnet eine Reddit-Seite und liest Beitragslisten (JSON, 100 pro Anfrage) bis `limit` Links."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('limit muss eine nichtnegative ganze Zahl sein')
        if ctx.sessions is None:
            raise ValueError('Reddit benötigt eine Browser-Session')
        if not limit:
            return []
        links, seen, after = [], set(), None
        with ctx.sessions.acquire(ctx.identity, 'reddit', ctx.country, ctx.mobile) as page:
            page.goto(page_url, wait_until='domcontentloaded')
            page.wait_for_timeout(2500)
            message = self.blocked_message(page)
            if message:
                raise PermanentFetchError(message)
            for _ in range(max(1, -(-limit // MAX_PER_CALL)) + 1):
                suffix = f'&limit={min(MAX_PER_CALL, limit - len(links) + 5)}&raw_json=1' + (f'&after={quote(after)}' if after else '')
                found, after = listing_links(self.fetch_json(page, json_path + suffix))
                for link in found:
                    if link not in seen:
                        seen.add(link)
                        links.append(link)
                if len(links) >= limit or not after:
                    break
                self._pause(page)
        return links[:limit]

    def channel_items(self, value, ctx, opts):
        """Beiträge eines Benutzers (`u/name`) oder Subreddits (`r/name`), neueste (oder beste) zuerst, 100 pro Anfrage.
        Reddit zeigt in jeder Liste höchstens etwa 1.000 Beiträge. Weitermachen über `opts.cursor` (die „after“-Marke)."""
        from .. import channel_list as cl
        value = str(value).strip()
        if value.lower().startswith('r/') or reddit_subreddit(value):
            sub = reddit_subreddit(value)
            if not sub:
                raise ValueError('Ungültiges Subreddit')
            top = opts.sort == 'top'
            page_url = f'{HOME}/r/{sub}/{"top" if top else "new"}/'
            json_path = f'/r/{sub}/top.json?t=all' if top else f'/r/{sub}/new.json?sort=new'
        else:
            name = reddit_user(value)
            if not name:
                raise ValueError('Ungültiger Reddit-Benutzername')
            page_url = f'{HOME}/user/{name}/submitted/?sort={"top" if opts.sort == "top" else "new"}'
            json_path = f'/user/{name}/submitted.json?sort={"top&t=all" if opts.sort == "top" else "new"}'
        if ctx.sessions is None:
            raise ValueError('Reddit benötigt eine Browser-Session')
        collector, result = cl.Collector(opts), cl.ListResult()
        after = opts.cursor or None
        with ctx.sessions.acquire(ctx.identity, 'reddit', ctx.country, ctx.mobile) as page:
            page.goto(page_url, wait_until='domcontentloaded')
            page.wait_for_timeout(2500)
            message = self.blocked_message(page)
            if message:
                raise PermanentFetchError(message)
            for _ in range(30):
                if opts.stop():
                    break
                suffix = '&limit=100&raw_json=1' + (f'&after={quote(after)}' if after else '')
                # Pakete mitten in einer Seite tragen die Marke, mit der DIESE Seite geholt wurde: Bricht der Lauf
                # dazwischen ab, holt der nächste dieselbe Seite noch einmal (Bekanntes wird übersprungen) statt
                # die restlichen Beiträge der Seite für immer auszulassen. Erst die fertige Seite trägt die nächste Marke.
                page_cursor = after
                entries, after = listing_entries(self.fetch_json(page, json_path + suffix))
                for entry in entries:
                    if opts.only_video and not entry.pop('is_video', False):
                        continue
                    entry.pop('is_video', None)
                    if not collector.add(entry, page_cursor):
                        break
                collector.flush(after)
                result.cursor = after
                if collector.done:
                    break
                if not after:
                    result.exhausted = True
                    break
                self._pause(page)
        collector.flush(after)
        result.found = collector.count
        result.reached_known = collector.reached_known
        if collector.hit_since:
            result.exhausted = True
        if result.exhausted:
            result.cursor = None
            result.note = 'Reddit zeigt in einer Liste höchstens etwa 1.000 Beiträge; ältere sind nicht erreichbar.'
        return result

    @with_retry(times=3, backoff=2.0)
    def profile_urls(self, username, ctx, limit=20):
        name = reddit_user(username)
        if not name:
            raise ValueError('Ungültiger Reddit-Benutzername (erlaubt: 3 bis 20 Zeichen aus Buchstaben, Zahlen, _ und -)')
        return self._listing(ctx, f'{HOME}/user/{name}/submitted/?sort=new',
                             f'/user/{name}/submitted.json?sort=new', limit)

    @with_retry(times=3, backoff=2.0)
    def search_urls(self, query, ctx, limit=20):
        """`r/name` = neue Beiträge dieses Subreddits, alles andere = Suche nach neuen Beiträgen."""
        query = (query or '').strip()
        if not query:
            raise ValueError('Suchbegriff fehlt')
        sub = reddit_subreddit(query)
        if sub:
            return self._listing(ctx, f'{HOME}/r/{sub}/new/', f'/r/{sub}/new.json?sort=new', limit)
        return self._listing(ctx, f'{HOME}/search/?q={quote(query)}&sort=new',
                             f'/search.json?q={quote(query)}&sort=new&type=link', limit)
