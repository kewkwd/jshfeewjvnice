"""Konten-Rotation: mehrere Konten je Plattform abwechselnd benutzen, Abkühlzeiten, Tageslimits, feste Proxys.

Ohne Fenster und ohne Netz testbar (außer `test_proxy`). Alle Funktionen bekommen eine offene sqlite3-Verbindung.

Auswahl (`pick_account`):
    default   das Standardkonto der Plattform (wie bisher, wenn kein Konto gewählt ist)
    fixed     ein bestimmtes Konto (`account_id`)
    rotate    unter allen Konten der Plattform mit „In Rotation“, angemeldet (Reddit auch ohne Anmeldung), nicht in
              Abkühlung, unter dem Tageslimit und nicht in `exclude`: das am längsten nicht benutzte zuerst
              (`last_used_at`), bei Gleichstand die niedrigere `use_count`, dann die kleinere `id`

Abkühlung nach Fehlern (`mark_failure`), steigend mit jeder Wiederholung in Folge (`fail_streak`):
    CAPTCHA / Sperre / Anmeldung abgelaufen   6 h -> 12 h -> 24 h -> 48 h (gedeckelt)
    zu viele Anfragen (429)                    1 h -> 2 h -> 4 h (gedeckelt)
    Proxy nicht erreichbar                     30 min
Ein Erfolg (`mark_success`) setzt die Wiederholungen zurück und hebt die Abkühlung auf.
„Anmeldung fehlt“ ist keine gespeicherte Abkühlung, sondern ergibt sich aus der fehlenden Anmeldedatei.
"""
import datetime
import ipaddress
import json
import logging
import pathlib
import re

from . import repo
from .net import accountproxy
from .net.errors import FinalMessage

log = logging.getLogger(__name__)

# Art -> (Anzeigetext, Abkühlzeiten in Stunden je Wiederholung)
COOLDOWN = {
    'captcha': ('CAPTCHA', (6, 12, 24, 48)),
    'block': ('Sperre', (6, 12, 24, 48)),
    'login': ('Anmeldung abgelaufen', (6, 12, 24, 48)),
    'ratelimit': ('zu viele Anfragen', (1, 2, 4)),
    'proxy': ('Proxy nicht erreichbar', (0.5,)),
}
LOGIN_LABEL = COOLDOWN['login'][0]
IP_URL = 'https://api.ipify.org/?format=text'


class NoAccount(RuntimeError):
    """Es ist gerade kein Konto verfügbar. Die Meldung nennt den frühesten Zeitpunkt, an dem wieder eins frei wird."""

    def __init__(self, message, until=None):
        super().__init__(FinalMessage(message))
        self.until = until


# ---------------------------------------------------------------- Zeit

def _now(now=None):
    return now if now is not None else repo.now()


def local_day(now):
    return now.astimezone().strftime('%Y-%m-%d')


def next_midnight(now):
    # Naive Ortszeit, die astimezone() mit dem Versatz des Zieltags auflöst (Sommer-/Winterzeit). Der feste Versatz
    # aus now.astimezone() läge am Tag der Zeitumstellung eine Stunde daneben (Tageslimit erst um 1 Uhr bzw. 23 Uhr frei).
    tomorrow = now.astimezone().date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(tomorrow, datetime.time()).astimezone(datetime.timezone.utc)


def fmt_when(moment, now=None):
    """Uhrzeit für die Anzeige in Ortszeit: „14:30“, „morgen 00:00“ oder „13.10. 14:30“."""
    now = _now(now)
    local, today = moment.astimezone(), now.astimezone()
    if local.date() == today.date():
        return local.strftime('%H:%M')
    if local.date() == (today + datetime.timedelta(days=1)).date():
        return 'morgen ' + local.strftime('%H:%M')
    return local.strftime('%d.%m. %H:%M')


def _parse(text):
    try:
        return repo.parse(text) if text else None
    except ValueError:
        return None


def platform_name(platform):
    from . import guicore
    return guicore.PLATFORMS.get(platform, {}).get('name', platform)


# ---------------------------------------------------------------- Zustand eines Kontos

def root_of(db, root=None):
    """Projektordner (dort liegt data/cookies): angegeben, sonst aus dem Pfad der Datenbank."""
    if root is not None:
        return pathlib.Path(root)
    path = accountproxy.db_file(db)
    if path is not None:
        from . import runlock
        return runlock.root_for_db(path)
    from . import guicore
    return guicore.ROOT


def has_login(account, root):
    """Gibt es eine gespeicherte Anmeldung? Reddit geht auch ohne."""
    if account['platform'] == 'reddit':
        return True
    from . import guicore
    return guicore.account_status(account['platform'], root, account['identity'])[0] == 'saved'


def used_today(account, now):
    if account.get('used_day') == local_day(now):
        return int(account.get('used_today') or 0)
    return 0


def account_state(account, now=None, root=None, *, login_available=None):
    """{'state': bereit|abkuehlung|tageslimit|keine_anmeldung, 'until': datetime|None, 'text': ...} (ohne Rotationsschalter)."""
    now = _now(now)
    if not (has_login(account, root) if login_available is None else login_available):
        return {'state': 'keine_anmeldung', 'until': None, 'text': 'keine Anmeldung'}
    until = _parse(account.get('cooldown_until'))
    if until is not None and until > now:
        reason = account.get('cooldown_reason') or 'Fehler'
        return {'state': 'abkuehlung', 'until': until,
                'text': f'Abkühlung bis {fmt_when(until, now)} wegen {reason}'}
    limit = account.get('daily_limit')
    if limit and used_today(account, now) >= int(limit):
        return {'state': 'tageslimit', 'until': next_midnight(now), 'text': 'Tageslimit erreicht'}
    return {'state': 'bereit', 'until': None, 'text': 'bereit'}


# ---------------------------------------------------------------- Auswahl

def pick_account(db, platform, *, mode='default', account_id=None, exclude=(), now=None, root=None, allowed=None):
    """Wählt ein Konto (siehe Modulbeschreibung). None, wenn keines gewählt werden kann."""
    now = _now(now)
    if mode == 'default':
        return repo.default_account(db, platform)
    if mode == 'fixed':
        account = repo.get_account(db, account_id) if account_id else None
        return account or repo.default_account(db, platform)
    if mode != 'rotate':
        raise ValueError('Unbekannter Kontomodus')
    root = root_of(db, root)
    ready = []
    for account in repo.list_accounts(db, platform):
        if not account.get('rotation_enabled', 1) or account['id'] in exclude:
            continue
        if allowed is not None and account['id'] not in allowed:
            continue
        if account_state(account, now, root)['state'] == 'bereit':
            ready.append(account)
    if not ready:
        return None
    ready.sort(key=lambda a: (a.get('last_used_at') or '', a.get('use_count') or 0, a['id']))
    return ready[0]


def no_account_message(db, platform, now=None, root=None, exclude=(), allowed=None):
    """(Meldung, frühester Zeitpunkt oder None), warum gerade kein Konto verfügbar ist."""
    message, until = _no_account(db, platform, now, root, exclude, allowed)
    return FinalMessage(message), until


def _no_account(db, platform, now, root, exclude, allowed):
    now = _now(now)
    root = root_of(db, root)
    name = platform_name(platform)
    accounts = [a for a in repo.list_accounts(db, platform)
                if a.get('rotation_enabled', 1) and (allowed is None or a['id'] in allowed)]
    if not accounts:
        return (f'Für {name} ist kein Konto in der Rotation. Schalte unter „Konten“ bei mindestens einem Konto '
                '„In Rotation“ ein.'), None
    states = [(a, account_state(a, now, root)) for a in accounts]
    if all(s['state'] == 'keine_anmeldung' for _a, s in states):
        return f'Für {name} ist kein Konto angemeldet. Bitte unter „Konten“ anmelden.', None
    waiting = [(s['until'], a, s) for a, s in states if s['until'] is not None]
    waiting.sort(key=lambda item: item[0])
    if any(s['state'] == 'bereit' and a['id'] in exclude for a, s in states):
        return (f'Alle verfügbaren Konten für {name} wurden für dieses Video schon versucht. '
                'Probiere es später noch einmal.'), (waiting[0][0] if waiting else None)
    if not waiting:
        return f'Für {name} ist gerade kein Konto verfügbar. Bitte unter „Konten“ nachsehen.', None
    until, account, state = waiting[0]
    reason = state['text'].split(' wegen ', 1)[1] if ' wegen ' in state['text'] else state['text']
    return (f'Alle Konten für {name} sind gerade nicht verfügbar. Das nächste ist wieder frei ab {fmt_when(until, now)} '
            f'(Konto „{account["label"]}“: {reason}).'), until


# ---------------------------------------------------------------- Markieren

def mark_used(db, account_id, now=None):
    """Ein Abruf mit diesem Konto: letzte Benutzung, Zähler gesamt und heute."""
    now = _now(now)
    account = repo.get_account(db, account_id)
    if account is None:
        return
    count = used_today(account, now) + 1
    db.execute('UPDATE accounts SET last_used_at=?, use_count=COALESCE(use_count,0)+1, used_today=?, used_day=? WHERE id=?',
               (repo.iso(now), count, local_day(now), account_id))
    db.commit()


def mark_failure(db, account_id, reason, kind=None, now=None):
    """Konto geht in die Abkühlzeit (siehe Modulbeschreibung). Gibt den Zeitpunkt (ISO) zurück."""
    now = _now(now)
    if kind is None:
        kind = classify(reason)
    if kind not in COOLDOWN:
        raise ValueError('Unbekannte Fehlerart')
    account = repo.get_account(db, account_id)
    if account is None:
        return None
    streak = int(account.get('fail_streak') or 0) + 1
    label, hours = COOLDOWN[kind]
    until = now + datetime.timedelta(hours=hours[min(streak, len(hours)) - 1])
    old = _parse(account.get('cooldown_until'))
    if old is not None and old > until:
        until, label = old, account.get('cooldown_reason') or label
    db.execute('UPDATE accounts SET fail_streak=?, cooldown_until=?, cooldown_reason=? WHERE id=?',
               (streak, repo.iso(until), label, account_id))
    db.commit()
    log.warning('Konto „%s“ pausiert bis %s (%s)', account['label'], repo.iso(until), label)
    return repo.iso(until)


def mark_success(db, account_id):
    db.execute('UPDATE accounts SET fail_streak=0, cooldown_until=NULL, cooldown_reason=NULL WHERE id=?', (account_id,))
    db.commit()


def clear_cooldown(db, account_id):
    """„Abkühlung aufheben“: das Konto ist sofort wieder dran."""
    mark_success(db, account_id)


def clear_login_cooldown(db, identity):
    """Nach erfolgreicher Anmeldung: eine Abkühlung wegen abgelaufener Anmeldung entfällt."""
    row = db.execute('SELECT id, cooldown_reason FROM accounts WHERE identity=?', (identity,)).fetchone()
    if row and row[1] == LOGIN_LABEL:
        clear_cooldown(db, row[0])


# ---------------------------------------------------------------- Fehlerart erkennen

# Ein einzelnes 403 (regionale Sperre, privates oder entferntes Video ...) ist ein Problem des Videos, nicht des Kontos:
# Das Video wird uebersprungen, das Konto bleibt im Einsatz. Erst wenn mehrere verschiedene Videos hintereinander mit 403
# scheitern (FORBIDDEN_LIMIT), gilt es als Kontosperre (`Rotator.classify`).
_KEY_KIND = {'captcha': 'captcha', 'sperre': 'block', 'bot': 'block', 'konto': 'block', 'anmeldung': 'login',
             '429': 'ratelimit', 'proxy': 'proxy'}
FORBIDDEN_LIMIT = 3


def classify(error, has_proxy=False):
    """Art eines Kontofehlers (Schlüssel aus COOLDOWN) oder None, wenn das Konto nichts dafür kann.

    `proxy` zählt nur bei einem Konto mit festem Proxy (`has_proxy`): sonst ist es Sache der Proxy-Einstellungen."""
    from .net import errors
    from .net.session_guard import SessionExpired
    if isinstance(error, SessionExpired):
        return 'login'
    if isinstance(error, accountproxy.AccountProxyError):
        return 'proxy'
    if error is None:
        return None
    text = str(error)
    if 'Der feste Proxy' in text or 'fester Proxy vermerkt' in text:
        return 'proxy'
    if 'Anmeldung abgelaufen' in text or 'Session für' in text and 'nicht verfügbar' in text:
        return 'login'
    kind = _KEY_KIND.get(_key_of(error))
    if kind == 'proxy' and not has_proxy:
        return None
    return kind


def _key_of(error):
    """Fehlerschluessel aus net/errors (auch bei schon uebersetzten Meldungen) oder None."""
    from .net import errors
    text = str(error)
    key = errors._classify(error if isinstance(error, BaseException) else RuntimeError(text))[0]
    if key is None:   # schon uebersetzte Meldungen erkennen
        for candidate, message in errors.MESSAGES.items():
            if (candidate in _KEY_KIND or candidate == '403') and message[:40] in text:
                return candidate
        if 'zu viele anfragen' in text.lower():      # auch die Meldungen der Adapter (Reddit/X bremsen uns aus)
            return '429'
    return key


def is_forbidden(error):
    """Ein 403 / Zugriff verweigert ohne Hinweis auf CAPTCHA, Anmeldung oder Kontosperre: Problem des Videos."""
    return error is not None and _key_of(error) == '403'


def classify_for(rot, error, has_proxy=False, key=''):
    """`classify` mit Gedaechtnis fuer 403 (siehe `Rotator.classify`); ohne Rotator wie `classify`."""
    return rot.classify(error, has_proxy, key) if rot is not None else classify(error, has_proxy)


# ---------------------------------------------------------------- Ablauf eines Laufs

class Rotator:
    """Hält beim Abruf das aktuelle Konto, zählt die Videos je Block und wechselt bei Fehlern.

    mode default/fixed: ein Konto, kein Wechsel (Fehler werden trotzdem vermerkt).
    mode rotate: nach `every` Videos zum nächsten Konto (`video_done()` meldet True) und sofort bei
    Kontofehlern (`fault()` liefert das nächste Konto oder None). Ein Video wird je Konto höchstens einmal versucht.
    """

    def __init__(self, db, platform, mode='default', account_id=None, every=5, root=None, allowed=None, clock=None):
        self.db, self.platform, self.mode = db, platform, mode
        self.account_id, self.every = account_id, max(1, int(every or 1))
        self.root, self.allowed, self.clock = root, set(allowed) if allowed else None, clock or repo.now
        self.current = None
        self.in_block = 0
        self.tried = {}
        self.used = []          # Namen der benutzten Konten, in der Reihenfolge des ersten Einsatzes
        self.failures = []
        self._marked = set()
        self.forbidden = []     # verschiedene Videos, die hintereinander mit 403 scheiterten (Erfolg setzt zurueck)
        self.message = None     # warum gerade kein Konto verfügbar ist

    @property
    def rotating(self):
        return self.mode == 'rotate'

    def _set(self, account):
        if self.current is None or self.current['id'] != account['id']:
            self.forbidden = []     # der 403-Zähler gehört zum Konto, nicht zum Lauf
        self.current = account
        self.in_block = 0
        if account['label'] not in self.used:
            self.used.append(account['label'])
        log.info('Konto: %s', account['label'])

    def _pick(self, exclude=()):
        return pick_account(self.db, self.platform, mode=self.mode, account_id=self.account_id, exclude=exclude,
                            now=self.clock(), root=self.root, allowed=self.allowed)

    def begin(self):
        """Erstes Konto. Bei Rotation ohne verfügbares Konto: NoAccount mit klarer Meldung."""
        account = self._pick()
        if account is None:
            if self.rotating:
                message, until = no_account_message(self.db, self.platform, self.clock(), self.root, allowed=self.allowed)
                self.message = message
                raise NoAccount(message, until)
            return None
        self._set(account)
        return account

    def attempt(self):
        """Ein Abruf beginnt (zählt für Tageslimit und Reihenfolge)."""
        if self.current is not None:
            mark_used(self.db, self.current['id'], self.clock())

    def video_done(self):
        """Ein Video ist fertig. True, wenn der Block voll ist und gewechselt werden soll."""
        self.in_block += 1
        if not self.rotating:
            return False
        if self.in_block >= self.every:
            return True
        # Tageslimit des aktuellen Kontos erreicht: schon jetzt wechseln
        return account_state(repo.get_account(self.db, self.current['id']) or self.current, self.clock(),
                             root_of(self.db, self.root))['state'] == 'tageslimit'

    def success(self):
        self.forbidden = []
        if self.current is not None:
            mark_success(self.db, self.current['id'])

    def classify(self, error, has_proxy=False, key=''):
        """Wie `rotation.classify`, aber mit Gedaechtnis fuer 403: Ein einzelnes 403 ist ein Videoproblem (None). Scheitern
        FORBIDDEN_LIMIT verschiedene Videos hintereinander mit 403 (kein Erfolg dazwischen), zaehlt es als Kontosperre
        ('block'); der Zaehler beginnt danach neu."""
        kind = classify(error, has_proxy)
        if kind is None and is_forbidden(error):
            if key not in self.forbidden:
                self.forbidden.append(key)
            if len(self.forbidden) >= FORBIDDEN_LIMIT:
                self.forbidden = []
                return 'block'
        return kind

    def next_block(self):
        """Nächstes Konto für den nächsten Block (kann dasselbe sein, wenn kein anderes bereit ist)."""
        account = self._pick()
        if account is None:
            self.message, _ = no_account_message(self.db, self.platform, self.clock(), self.root, allowed=self.allowed)
            return None
        self._set(account)
        return account

    def fault(self, kind, key=''):
        """Das aktuelle Konto ist ausgefallen (Abkühlung). Bei Rotation: das nächste Konto, das dieses Video noch
        nicht versucht hat, sonst None (`message` sagt dann, warum)."""
        current = self.current
        if current is None:
            return None
        if current['id'] not in self._marked:      # je Lauf nur einmal vermerken (sonst wächst die Abkühlung pro Video)
            self._marked.add(current['id'])
            if kind in COOLDOWN:                   # „missing“ (Anmeldung fehlt) ist keine Abkühlung: das Konto wird nur übergangen
                mark_failure(self.db, current['id'], '', kind, self.clock())
            self.failures.append((current['label'], kind))
        if not self.rotating:
            return None
        tried = self.tried.setdefault(key, set())
        tried.add(current['id'])
        account = self._pick(exclude=tried)
        if account is None:
            self.message, _ = no_account_message(self.db, self.platform, self.clock(), self.root, exclude=tried,
                                                 allowed=self.allowed)
            return None
        self._set(account)
        return account

    def used_labels(self):
        return list(self.used)

    def used_text(self):
        return ', '.join(self.used)


# ---------------------------------------------------------------- Anzeige

def rotation_status(db, platform=None, now=None, root=None):
    """Je Konto: Zustand für die Anzeige (bereit / Abkühlung bis … / Tageslimit erreicht / keine Anmeldung)."""
    now = _now(now)
    root = root_of(db, root)
    rows = []
    for account in repo.list_accounts(db, platform):
        state = account_state(account, now, root)
        limit = account.get('daily_limit')
        rows.append({
            'id': account['id'], 'platform': account['platform'], 'label': account['label'],
            'identity': account['identity'], 'is_default': bool(account['is_default']),
            'in_rotation': bool(account.get('rotation_enabled', 1)),
            'state': state['state'], 'text': state['text'],
            'until': repo.iso(state['until']) if state['until'] else None,
            'cooldown_reason': account.get('cooldown_reason') if state['state'] == 'abkuehlung' else None,
            'used_today': used_today(account, now), 'daily_limit': int(limit) if limit else None,
            'use_count': int(account.get('use_count') or 0), 'last_used_at': account.get('last_used_at'),
            'has_proxy': bool(account.get('proxy')), 'proxy': account.get('proxy') or '',
        })
    return rows


def _plural(n, one, many):
    return f'{n} {one if n == 1 else many}'


def summary(rows, now=None):
    """Kopfzeile je Plattform: „3 Konten · 2 bereit · 1 in Abkühlung bis 14:30“."""
    now = _now(now)
    parts = [_plural(len(rows), 'Konto', 'Konten')]
    active = [r for r in rows if r['in_rotation']]
    ready = [r for r in active if r['state'] == 'bereit']
    cooling = [r for r in active if r['state'] == 'abkuehlung']
    limited = [r for r in active if r['state'] == 'tageslimit']
    nologin = [r for r in active if r['state'] == 'keine_anmeldung']
    if len(rows) > 0:
        parts.append(f'{len(ready)} bereit')
    if cooling:
        earliest = min(_parse(r['until']) for r in cooling)
        parts.append(f'{len(cooling)} in Abkühlung bis {fmt_when(earliest, now)}')
    if limited:
        parts.append(f'{len(limited)} am Tageslimit')
    if nologin:
        parts.append(f'{len(nologin)} ohne Anmeldung')
    if len(active) != len(rows):
        parts.append(f'{len(rows) - len(active)} nicht in Rotation')
    return ' · '.join(parts)


def ip_hints(rows):
    """Hinweise (keine Sperren) zu gleicher Ausgangs-IP, wenn mehrere Konten rotieren. Höchstens zwei Sätze."""
    active = [r for r in rows if r['in_rotation']]
    if len(active) < 2:
        return []
    hints = []
    by_endpoint = {}
    for row in active:
        end = accountproxy.endpoint(row['proxy']) if row['proxy'] else None
        if end:
            by_endpoint.setdefault(end, []).append(row['label'])
    shared = [names for names in by_endpoint.values() if len(names) > 1]
    if shared:
        names = ' und '.join(f'„{n}“' for n in shared[0][:3])
        hints.append(f'Die Konten {names} benutzen denselben Proxy – für die Plattform sieht das nach derselben IP aus.')
    with_proxy = [r for r in active if r['proxy']]
    without = [r for r in active if not r['proxy']]
    if with_proxy and without:
        names = ', '.join(f'„{r["label"]}“' for r in without[:3])
        hints.append(f'{names} {"hat" if len(without) == 1 else "haben"} keinen eigenen Proxy und {"nutzt" if len(without) == 1 else "nutzen"} '
                     'deine normale Verbindung – die anderen nicht.')
    return hints[:2]


# ---------------------------------------------------------------- Fester Proxy je Konto

def set_account_proxy(db, account_id, raw):
    """Legt den festen Proxy eines Kontos fest (prüft das Format, speichert das Passwort nur in
    data/account_proxies.json, in die Datenbank kommt nur die maskierte Anzeige). Gibt die Anzeige zurück."""
    account = repo.get_account(db, account_id)
    if account is None:
        raise ValueError('Konto nicht gefunden')
    path = accountproxy.db_file(db)
    if path is None:
        raise ValueError('Ein fester Proxy braucht eine Datenbank-Datei.')
    shown = accountproxy.set_entry(path, account['identity'], raw)
    repo.set_account_proxy_ref(db, account_id, shown)
    return shown


def clear_account_proxy(db, account_id):
    account = repo.get_account(db, account_id)
    if account is None:
        return
    path = accountproxy.db_file(db)
    if path is not None:
        accountproxy.remove_entry(path, account['identity'])
    repo.set_account_proxy_ref(db, account_id, None)


def test_proxy(proxy, *, url=IP_URL, timeout=8.0, getter=None):
    """Prüft einen Proxy mit einem HTTPS-Aufruf (Zertifikat wird geprüft); es meldet sich dabei kein Konto an.
    Gibt (ok, Meldung, Ausgangs-IP) zurück; die Meldung enthält nie das Passwort. Kann dauern: nicht im Fenster-Thread."""
    from .net import tunnel
    from .net.proxy import Proxy
    try:
        item = proxy if isinstance(proxy, Proxy) else Proxy.parse(accountproxy.normalize(proxy))
    except ValueError as error:
        return False, accountproxy.scrub(str(error)), None
    shown = f'{item.host}:{item.port}'
    if item.scheme not in ('http', 'socks5'):
        return False, (f'Diesen Proxy-Typ ({item.scheme}) kann das Programm hier nicht testen. '
                       'Er kann trotzdem funktionieren.'), None
    getter = getter or tunnel.https_get
    try:
        status, body, seconds = getter(item, url, timeout=timeout)
    except tunnel.TlsBroken:
        return False, (f'Der Proxy {shown} greift in die verschlüsselte Verbindung ein '
                       '(Zertifikat ungültig). Nimm einen anderen Proxy.'), None
    except tunnel.TunnelError as error:
        text = str(error)
        if 'lehnt CONNECT ab' in text or 'Anmeldung abgelehnt' in text or 'verlangt Anmeldung' in text:
            return False, (f'Der Proxy {shown} lehnt die Verbindung ab. Stimmen Benutzername und Passwort?'), None
        return False, f'Der Proxy {shown} funktioniert nicht: {accountproxy.scrub(text)}', None
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError):
            return False, 'Die Proxy-Adresse ist ungültig.', None
        return False, (f'Der Proxy {shown} ist nicht erreichbar (keine Verbindung oder zu langsam). '
                       'Stimmen Adresse und Port?'), None
    text = (body or '').strip()
    try:
        ip = str(ipaddress.ip_address(text))
    except ValueError:
        return False, f'Der Proxy {shown} antwortet, aber nicht wie erwartet (HTTP {status}).', None
    if status != 200:
        return False, f'Der Proxy {shown} antwortet mit HTTP {status}.', None
    return True, f'Der Proxy funktioniert. Ausgangs-IP: {ip} ({int(seconds * 1000)} ms).', ip
