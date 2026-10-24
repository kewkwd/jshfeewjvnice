"""Plattform-Aufrufe für Aktionen. Ziel ist immer `plattform:comment_id`."""
import json, logging, re
from urllib.parse import quote

from . import ART_ANMELDUNG, ART_CAPTCHA, ART_FEHLER, ART_RATELIMIT, ART_SPERRE, ART_ZIEL, ART_UNKLAR, Ergebnis

log = logging.getLogger(__name__)
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/154.0.0.0 Safari/537.36')
ANMELDUNG = ('login', 'anmelden', 'not logged', 'logged out', 'auth')
CAPTCHA = ('captcha', 'verify', 'sicherheits', 'puzzle', 'arkose')
SPERRE = ('suspended', 'locked', 'gesperrt', 'blocked', 'eingeschränkt')
RATELIMIT = ('rate limit', 'too many', 'zu viele', '429')


def _trennen(uid):
    platform, _, cid = str(uid).partition(':')
    if not cid:
        raise ValueError('Ziel-Kennung ohne Kommentar-Nummer')
    return platform, cid


def _client(cookies, proxy, domain, *, json_body=False):
    import os
    if os.environ.get('SS_REQUIRE_PROXY') == '1' and not proxy:
        raise ValueError('Aktionsabruf ohne Proxy ist für diese Tests gesperrt.')
    from curl_cffi import requests as creq
    sitzung = creq.Session(impersonate='chrome')
    if proxy:
        if str(proxy).startswith('socks5://'):
            proxy = 'socks5h://' + str(proxy)[len('socks5://'):]
        sitzung.proxies = {'http': proxy, 'https': proxy}
    for cookie in cookies or []:
        try:
            sitzung.cookies.set(cookie['name'], cookie['value'],
                                domain=cookie.get('domain') or domain, path=cookie.get('path') or '/')
        except Exception:
            pass
    kopf = {'User-Agent': UA, 'Referer': f'https://{domain.lstrip(".")}/'}
    if json_body:
        kopf['Content-Type'] = 'application/json'
    sitzung.headers.update(kopf)
    return sitzung


def _cookie_wert(cookies, name):
    for cookie in cookies or []:
        if cookie.get('name') == name:
            return cookie.get('value')
    return None


def _urteil(antwort, *, ziel_fehler=(404,)):
    if 200 <= antwort.status_code < 300:
        try:
            data = antwort.json()
        except (ValueError, AttributeError):
            return Ergebnis(False, ART_UNKLAR, 'Die Plattform hat keine lesbare Aktionsbestätigung geliefert.')
        if isinstance(data, dict):
            failed = data.get('success') is False or data.get('ok') is False or data.get('status') in ('fail', 'error')
            failed = failed or bool(data.get('errors') or data.get('error'))
            failed = failed or data.get('status_code', 0) not in (0, '0', None)
            if isinstance(data.get('json'), dict):
                failed = failed or bool(data['json'].get('errors'))
            if failed:
                return Ergebnis(False, ART_FEHLER, 'Die Plattform hat die Aktion abgelehnt.')
            confirmed = data.get('success') is True or data.get('ok') is True or data.get('status') in ('ok', 'success')
            confirmed = confirmed or ('status_code' in data and data['status_code'] in (0, '0'))
            confirmed = confirmed or (isinstance(data.get('json'), dict) and data['json'].get('errors') == [])
            confirmed = confirmed or (isinstance(data.get('data'), dict) and bool(data['data']))
            if confirmed:
                return Ergebnis(True, 'ok', 'erledigt')
        return Ergebnis(False, ART_UNKLAR, 'Die Antwort enthält keine eindeutige Aktionsbestätigung.')
    text = (antwort.text or '')[:4000].lower()
    code = antwort.status_code
    if code == 429 or any(m in text for m in RATELIMIT):
        return Ergebnis(False, ART_RATELIMIT, 'Zu viele Anfragen.')
    if any(m in text for m in CAPTCHA):
        return Ergebnis(False, ART_CAPTCHA, 'Sicherheitsabfrage.')
    if code in (401, 403) and any(m in text for m in ANMELDUNG):
        return Ergebnis(False, ART_ANMELDUNG, 'Anmeldung abgelaufen.')
    if any(m in text for m in SPERRE):
        return Ergebnis(False, ART_SPERRE, 'Konto gesperrt oder eingeschränkt.')
    if code in ziel_fehler:
        return Ergebnis(False, ART_ZIEL, 'Ziel nicht mehr vorhanden.')
    return Ergebnis(False, ART_FEHLER, f'Status {code}.')


# ---- TikTok ----
def _tiktok(cookies, proxy):
    sitzung = _client(cookies, proxy, '.tiktok.com')
    sitzung.headers['Accept'] = 'application/json, text/plain, */*'
    return sitzung


def tiktok_like(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_tiktok(cookies, proxy).post(
        'https://www.tiktok.com/api/comment/like/',
        data={'aweme_id': (video or '').partition(':')[2], 'cid': cid, 'type': 1, 'aid': 1988}, timeout=30))


def tiktok_report(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_tiktok(cookies, proxy).post(
        'https://www.tiktok.com/api/report/',
        data={'object_id': cid, 'object_type': 2, 'aweme_id': (video or '').partition(':')[2],
              'report_type': grund or '1', 'aid': 1988}, timeout=30))


def tiktok_kommentar(*, cookies, proxy, ziel, video, text, grund=None, eltern=None, sterne=None, **_):
    if not video:
        return Ergebnis(False, ART_ZIEL, 'Das zugehörige Video fehlt.')
    return _urteil(_tiktok(cookies, proxy).post(
        'https://www.tiktok.com/api/comment/publish/',
        data={'aweme_id': str(video).partition(':')[2], 'text': text or '', 'comment_id': eltern or str(ziel).partition(':')[2],
              'aid': 1988, 'app_language': 'de-DE'}, timeout=30))


# ---- Instagram ----
def _instagram(cookies, proxy):
    sitzung = _client(cookies, proxy, '.instagram.com')
    sitzung.headers.update({'X-CSRFToken': _cookie_wert(cookies, 'csrftoken') or '',
                            'X-IG-App-ID': '936619743392459', 'X-Requested-With': 'XMLHttpRequest'})
    return sitzung


def instagram_like(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_instagram(cookies, proxy).post('https://www.instagram.com/web/comments/like/',
                                                   data={'comment_id': cid}, timeout=30))


def instagram_report(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_instagram(cookies, proxy).post(f'https://www.instagram.com/web/comments/{cid}/report/',
                                                   data={'reason': grund or '1'}, timeout=30))


def instagram_kommentar(*, cookies, proxy, ziel, video, text, grund=None, eltern=None, sterne=None, **_):
    if not video:
        return Ergebnis(False, ART_ZIEL, 'Das zugehörige Video fehlt.')
    return _urteil(_instagram(cookies, proxy).post(
        f'https://www.instagram.com/web/comments/{str(video).partition(":")[2]}/add/',
        data={'comment_text': text or '', 'replied_to_comment_id': eltern or str(ziel).partition(':')[2]}, timeout=30))


# ---- YouTube (InnerTube) ----
_INNERTUBE = {'key': None, 'context': None}


def _youtube(cookies, proxy):
    sitzung = _client(cookies, proxy, '.youtube.com', json_body=True)
    sitzung.headers['X-Youtube-Client-Name'] = '1'
    if not _INNERTUBE['key']:
        treffer = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', sitzung.get('https://www.youtube.com/', timeout=30).text)
        if not treffer:
            raise RuntimeError('YouTube-Schnittstellenschlüssel nicht gefunden.')
        _INNERTUBE['key'] = treffer.group(1)
        _INNERTUBE['context'] = {'client': {'clientName': 'WEB', 'clientVersion': '2.20250101.00.00', 'hl': 'de'}}
    sitzung.headers['X-Youtube-Client-Version'] = '2.20250101.00.00'
    return sitzung


def _youtube_aktion(cookies, proxy, video, comment, aktion):
    sitzung = _youtube(cookies, proxy)
    koerper = {'context': _INNERTUBE['context'],
               'target': {'videoId': (video or '').partition(':')[2], 'commentId': comment},
               'actions': [aktion]}
    return _urteil(sitzung.post(f"https://www.youtube.com/youtubei/v1/comment/perform_comment_action"
                                f"?key={_INNERTUBE['key']}", data=json.dumps(koerper), timeout=30))


def youtube_like(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _youtube_aktion(cookies, proxy, video, cid, 'like')


def youtube_report(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _youtube_aktion(cookies, proxy, video, cid, 'flag')


def youtube_kommentar(*, cookies, proxy, ziel, video, text, grund=None, eltern=None, sterne=None, **_):
    if not video:
        return Ergebnis(False, ART_ZIEL, 'Das zugehörige Video fehlt.')
    sitzung = _youtube(cookies, proxy)
    video_id = str(video).partition(':')[2]
    koerper = {'context': _INNERTUBE['context'], 'videoId': video_id,
               'commentText': {'runs': [{'text': text or ''}]}}
    if eltern:
        koerper['target'] = {'videoId': video_id, 'commentId': eltern}
    return _urteil(sitzung.post(f"https://www.youtube.com/youtubei/v1/comment/create_comment"
                                f"?key={_INNERTUBE['key']}", data=json.dumps(koerper), timeout=30))


# ---- Reddit ----
def _reddit(cookies, proxy):
    sitzung = _client(cookies, proxy, '.reddit.com')
    sitzung.headers['Accept'] = 'application/json'
    return sitzung


def reddit_like(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_reddit(cookies, proxy).post('https://www.reddit.com/api/vote',
                                                data={'id': f't1_{cid}', 'dir': 1, 'rank': 2}, timeout=30))


def reddit_report(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    return _urteil(_reddit(cookies, proxy).post('https://www.reddit.com/api/report',
                                                data={'thing_id': f't1_{cid}', 'reason': grund or 'spam'},
                                                timeout=30))


def reddit_kommentar(*, cookies, proxy, ziel, video, text, grund=None, eltern=None, sterne=None, **_):
    comment = eltern or str(ziel).partition(':')[2]
    return _urteil(_reddit(cookies, proxy).post(
        'https://www.reddit.com/api/comment',
        data={'thing_id': comment if comment.startswith('t1_') else f't1_{comment}', 'text': text or ''}, timeout=30))


# ---- X ----
_X_BASIS = 'https://x.com/i/api/graphql'
_X_BEARER = ('Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D'
             '1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')


def _x(cookies, proxy):
    sitzung = _client(cookies, proxy, '.x.com', json_body=True)
    sitzung.headers.update({'Authorization': _X_BEARER, 'X-Csrf-Token': _cookie_wert(cookies, 'ct0') or ''})
    return sitzung


def _x_query(sitzung, name):
    zwischen = _x_query.__dict__.setdefault('_cache', {})
    if name in zwischen:
        return zwischen[name]
    seite = sitzung.get('https://x.com/', timeout=30).text
    treffer = (re.findall(r'"queryId":"(\d{15,25})"[^}]{0,300}?' + name, seite)
               or re.findall(name + r'[^}]{0,300}?"queryId":"(\d{15,25})"', seite))
    if not treffer:
        raise RuntimeError(f'X-Query-Kennung für {name} nicht gefunden.')
    zwischen[name] = treffer[0]
    return treffer[0]


def x_like(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    sitzung = _x(cookies, proxy)
    qid = _x_query(sitzung, 'FavoriteTweet')
    return _urteil(sitzung.post(f'{_X_BASIS}/{qid}/FavoriteTweet',
                                data=json.dumps({'variables': {'tweet_id': cid}, 'queryId': qid}), timeout=30))


def x_report(*, cookies, proxy, ziel, video, text=None, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    sitzung = _x(cookies, proxy)
    kopf = dict(sitzung.headers)
    kopf['Content-Type'] = 'application/x-www-form-urlencoded'
    return _urteil(sitzung.post('https://x.com/i/api/1.1/statuses/report.json',
                                data=f'tweet_id={quote(cid)}&reason={quote(grund or "spam")}',
                                headers=kopf, timeout=30))


def x_kommentar(*, cookies, proxy, ziel, video, text, grund=None, eltern=None, sterne=None, **_):
    _p, cid = _trennen(ziel)
    sitzung = _x(cookies, proxy)
    qid = _x_query(sitzung, 'CreateTweet')
    variablen = {'tweet_text': text or '', 'reply': {'in_reply_to_tweet_id': cid, 'exclude_reply_user_ids': []},
                 'media_entities': [], 'possibly_sensitive': False, 'semantic_annotation_ids': [],
                 'dark_request': False}
    return _urteil(sitzung.post(f'{_X_BASIS}/{qid}/CreateTweet',
                                data=json.dumps({'variables': variablen, 'queryId': qid}), timeout=30))
