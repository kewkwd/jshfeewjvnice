"""Automatische Aktionen: Kommentare liken, melden, kommentieren, bewerten."""
import datetime, hashlib, logging, random, time
from dataclasses import dataclass

from .. import repo, rotation
from ..store import Store

log = logging.getLogger(__name__)

ARTEN = ('like', 'report', 'kommentar', 'bewertung')
BRAUCHT_BROWSER = {'google', 'trustpilot'}
STATUS = ('offen', 'laeuft', 'erledigt', 'fehler', 'uebersprungen')
MAX_VERSUCHE = 5
ART_CAPTCHA, ART_SPERRE, ART_ANMELDUNG = 'captcha', 'block', 'login'
ART_RATELIMIT, ART_PROXY = 'ratelimit', 'proxy'
ART_ZIEL, ART_FEHLER = 'ziel', 'fehler'
ART_UNKLAR = 'unklar'

DDL = """
CREATE TABLE IF NOT EXISTS aktionen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL, art TEXT NOT NULL,
    ziel_typ TEXT NOT NULL DEFAULT 'kommentar', ziel_uid TEXT NOT NULL, video_uid TEXT, eltern_id TEXT,
    text TEXT, grund TEXT, sterne INTEGER, status TEXT NOT NULL DEFAULT 'offen',
    versuche INTEGER NOT NULL DEFAULT 0, faellig_am TEXT, idempotenz TEXT NOT NULL UNIQUE,
    fehler TEXT, erstellt_am TEXT NOT NULL, erledigt_am TEXT, last_account_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_aktionen_faellig ON aktionen (status, faellig_am);
CREATE INDEX IF NOT EXISTS idx_aktionen_konto   ON aktionen (account_id, status);
"""

def _migrate_rotation_bindings(db):
    # Parse the account suffix after the exact target. A target may itself
    # contain ':rot:'; that must never turn a user-selected account into rotation.
    prefix="platform||':'||art||':'||ziel_uid||':'"
    db.execute('UPDATE aktionen SET last_account_id=COALESCE(last_account_id,account_id), account_id=NULL '
        f'WHERE substr(idempotenz,1,length({prefix}))=({prefix}) '
        f"AND substr(idempotenz,length({prefix})+1,4) IN ('rot','rot:')")


def ensure_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS action_attempts (id INTEGER PRIMARY KEY, action_id INTEGER, '
               'account_id INTEGER, started_at TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_action_attempt_time ON action_attempts(started_at)')
    existing = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='aktionen'").fetchone()
    required = {'id','platform','account_id','art','ziel_typ','ziel_uid','video_uid','eltern_id','text',
                'grund','sterne','status','versuche','faellig_am','idempotenz','fehler','erstellt_am','erledigt_am'}
    columns = {z[1] for z in db.execute('PRAGMA table_info(aktionen)')}
    legacy_check = existing and 'CHECK' in existing[0].upper() and "'like'" in existing[0].lower()
    if existing and required <= columns and not legacy_check:
        if 'last_account_id' not in columns:
            db.execute('ALTER TABLE aktionen ADD COLUMN last_account_id INTEGER')
            _migrate_rotation_bindings(db)
            db.commit()
        return
    # executescript commits implicitly. A savepoint plus individual statements
    # keeps the entire migration reversible if old data fails a constraint.
    db.execute('SAVEPOINT action_schema')
    try:
        create = DDL.strip().split(';')[0]
        if existing:
            if not {'id','platform','art','ziel_uid','idempotenz','erstellt_am'} <= columns:
                raise ValueError('Die Aktionsliste hat ein unbekanntes Format; die vorhandenen Daten bleiben erhalten.')
            seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='aktionen'").fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone() else None
            db.execute(create.replace('IF NOT EXISTS aktionen', 'aktionen_neu'))
            shared = [z[1] for z in db.execute('PRAGMA table_info(aktionen_neu)') if z[1] in columns]
            names = ','.join(shared)
            db.execute(f'INSERT INTO aktionen_neu ({names}) SELECT {names} FROM aktionen')
            db.execute('DROP TABLE aktionen')
            db.execute('ALTER TABLE aktionen_neu RENAME TO aktionen')
            if 'last_account_id' not in columns:
                _migrate_rotation_bindings(db)
            if seq:
                db.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='aktionen'", (seq[0],))
        else:
            db.execute(create)
        for statement in DDL.strip().split(';')[1:]:
            if statement.strip():
                db.execute(statement)
        db.execute('RELEASE action_schema')
    except BaseException:
        db.execute('ROLLBACK TO action_schema')
        db.execute('RELEASE action_schema')
        raise


@dataclass
class Ergebnis:
    ok: bool
    art: str = ART_FEHLER
    meldung: str = ''


def enqueue(db, platform, art, ziel_uid, *, account_id=None, video_uid=None, eltern_id=None,
            text=None, grund=None, sterne=None, now=None):
    if art not in ARTEN:
        raise ValueError(f'Unbekannte Aktion: {art}')
    if platform not in repo.ACCOUNT_PLATFORMS:
        raise ValueError('Unbekannte Plattform.')
    if (art == 'bewertung') != (platform in BRAUCHT_BROWSER):
        raise ValueError('Bewertungen sind für Google und Trustpilot verfügbar; Kommentaraktionen für die übrigen Plattformen.')
    if not ziel_uid or ':' not in str(ziel_uid) or not str(ziel_uid).startswith(platform + ':'):
        raise ValueError('Die Ziel-Kennung muss wie „plattform:ziel“ aussehen.')
    if not str(ziel_uid).partition(':')[2].strip():
        raise ValueError('Die Ziel-Kennung darf nicht leer sein.')
    if art == 'kommentar' and (not isinstance(text, str) or not text.strip()):
        raise ValueError('Ein Kommentar braucht einen Text.')
    if video_uid and not str(video_uid).startswith(platform + ':'):
        raise ValueError('Die Video-Kennung gehört zu einer anderen Plattform.')
    if account_id is not None:
        account = repo.get_account(db, account_id)
        if account is None or account['platform'] != platform:
            raise ValueError('Das gewählte Konto gehört nicht zur Zielplattform.')
    if art == 'bewertung' and sterne is not None and not 1 <= int(sterne) <= 5:
        raise ValueError('Sterne müssen zwischen 1 und 5 liegen.')
    ensure_schema(db)
    if art == 'kommentar' and not eltern_id:
        eltern_id = str(ziel_uid).partition(':')[2]
    ziel_typ = 'ort' if art == 'bewertung' else 'kommentar'
    jetzt = repo.iso(now or repo.now())
    kennung = f'{platform}:{art}:{ziel_uid}:{account_id or "rot"}'
    if art == 'kommentar' and text:
        kennung += ':' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]
    cur = db.execute(
        'INSERT OR IGNORE INTO aktionen (platform, account_id, art, ziel_typ, ziel_uid, video_uid, eltern_id,'
        ' text, grund, sterne, status, faellig_am, idempotenz, erstellt_am)'
        " VALUES (?,?,?,?,?,?,?,?,?,?,'offen',?,?,?)",
        (platform, account_id, art, ziel_typ, ziel_uid, video_uid, eltern_id, text, grund,
         int(sterne) if sterne else None, jetzt, kennung, jetzt))
    db.commit()
    return bool(cur.rowcount)


def enqueue_comments(db, platform, comment_uids, *, art='like', video_uid=None, grund=None, text=None,
                     skip_authors=None, now=None):
    verboten = {a.casefold() for a in (skip_authors or [])}
    neu = 0
    for uid in comment_uids:
        autor = _feld(db, 'author', platform, uid)
        if verboten and autor and autor.casefold() in verboten:
            continue
        if enqueue(db, platform, art, uid, video_uid=video_uid or _video_von(db, platform, uid),
                   grund=grund, text=text, now=now):
            neu += 1
    return neu


def _feld(db, spalte, platform, uid):
    zeile = db.execute(f'SELECT {spalte} FROM comments WHERE platform=? AND uid=?', (platform, uid)).fetchone()
    return zeile[0] if zeile else None


def _video_von(db, platform, uid):
    video_id = _feld(db, 'video_id', platform, uid)
    return f'{platform}:{video_id}' if video_id else None


def pending(db, *, art=None, platform=None, limit=50, now=None):
    ensure_schema(db)
    bedingungen = ["status IN ('offen','fehler')", '(faellig_am IS NULL OR faellig_am<=?)', 'versuche<?']
    werte = [repo.iso(now or repo.now()), MAX_VERSUCHE]
    if art:
        bedingungen.append('art=?'); werte.append(art)
    if platform:
        bedingungen.append('platform=?'); werte.append(platform)
    cur = db.execute('SELECT * FROM aktionen WHERE ' + ' AND '.join(bedingungen) +
                     ' ORDER BY COALESCE(faellig_am, erstellt_am), id LIMIT ?', werte + [int(limit)])
    spalten = [c[0] for c in cur.description]
    return [dict(zip(spalten, r)) for r in cur.fetchall()]


def counts(db):
    ensure_schema(db)
    return {s: db.execute('SELECT COUNT(*) FROM aktionen WHERE status=?', (s,)).fetchone()[0] for s in STATUS}


from ..operation import exclusive

@exclusive
def run(db_path, *, limit=50, art=None, platform=None, dry=False, pause=(2.0, 8.0), now=None,
        cfg=None, melden=log.info, stop=None, takt_pro_minute=None):
    from . import endpunkte
    from .. import streuung
    from ..net import proxymode
    if cfg is None:
        from ..config import load
        cfg = load()
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('Die Anzahl muss eine positive ganze Zahl sein.')
    proxymode.require_no_anon(cfg, 'Aktionen')
    takt_pro_minute = streuung.TAKT_PRO_MINUTE if takt_pro_minute is None else takt_pro_minute
    sitzung = None
    store = Store(db_path)
    db = store.db
    zusammenfassung = {'erledigt': 0, 'fehler': 0, 'uebersprungen': 0, 'abgebrochen': False,
                       'wartezeit': 0.0, 'gruende': {}}
    try:
        offen = pending(db, art=art, platform=platform, limit=limit, now=now)
        if not offen:
            melden('Keine fälligen Aktionen.')
            return zusammenfassung
        random.shuffle(offen)
        sitzung = None if dry else _Runtime(cfg)
        for aktion in offen:
            if stop is not None and stop():
                zusammenfassung['abgebrochen'] = True
                break
            konto = _konto_waehlen(db, aktion, now=now)
            if konto is None:
                if not dry:
                    _verschieben(db, aktion['id'], 'kein Konto frei', sekunden=600)
                zusammenfassung['gruende']['kein Konto frei'] = zusammenfassung['gruende'].get('kein Konto frei', 0) + 1
                continue
            if not streuung.im_fenster(konto, now):
                if not dry:
                    _verschieben(db, aktion['id'], 'außerhalb des Aktivitätsfensters', sekunden=1800)
                zusammenfassung['gruende']['ausserhalb Fenster'] = \
                    zusammenfassung['gruende'].get('ausserhalb Fenster', 0) + 1
                continue
            if dry:
                melden(f"[Probe] {aktion['art']} {aktion['ziel_uid']} mit {konto['label']}")
                continue
            from ..operation import wait
            if takt_pro_minute and not wait(streuung.takt_wartezeit(db_path, takt_pro_minute=takt_pro_minute, now=now), stop):
                zusammenfassung['abgebrochen'] = True
                break
            # Persist before sending: an interrupted process must not silently
            # repeat an external mutation when the app starts again.
            claimed = db.execute("UPDATE aktionen SET status='laeuft', last_account_id=?, versuche=versuche+1 "
                                 "WHERE id=? AND status IN ('offen','fehler')",
                                 (konto['id'], aktion['id']))
            db.commit()
            if not claimed.rowcount:
                continue
            db.execute('INSERT INTO action_attempts(action_id,account_id,started_at) VALUES (?,?,?)',
                       (aktion['id'],konto['id'],repo.iso(now or repo.now())))
            db.commit()
            ergebnis = _ausfuehren(sitzung, konto, aktion)
            if ergebnis.ok:
                db.execute("UPDATE aktionen SET status='erledigt', erledigt_am=?, fehler=NULL WHERE id=?",
                           (repo.iso(now or repo.now()), aktion['id']))
                db.commit()
                rotation.mark_used(db, konto['id'], now)
                zusammenfassung['erledigt'] += 1
                melden(f"{aktion['art']} {aktion['ziel_uid']} erledigt ({konto['label']})")
            else:
                _fehler(db, aktion, konto, ergebnis, now=now)
                zusammenfassung['fehler'] += 1
                zusammenfassung['gruende'][ergebnis.art] = zusammenfassung['gruende'].get(ergebnis.art, 0) + 1
                melden(f"{aktion['art']} {aktion['ziel_uid']} fehlgeschlagen: {ergebnis.meldung}")
            ruhe = random.uniform(*pause) + streuung.versatz(konto['identity']) / 30.0
            if takt_pro_minute:
                ruhe = max(ruhe, streuung.takt_wartezeit(db_path, takt_pro_minute=takt_pro_minute, now=now))
            from ..operation import wait
            if not wait(ruhe, stop):
                zusammenfassung['abgebrochen'] = True
                break
            zusammenfassung['wartezeit'] += ruhe
        return zusammenfassung
    finally:
        if sitzung is not None:
            sitzung.close()
        store.close()


def _konto_waehlen(db, aktion, now=None):
    if aktion.get('account_id'):
        konto = repo.get_account(db, aktion['account_id'])
        if konto is None or konto['platform'] != aktion['platform']:
            return None
        if rotation.account_state(konto, now, rotation.root_of(db))['state'] != 'bereit':
            return None
        return konto
    return rotation.pick_account(db, aktion['platform'], mode='rotate', exclude=set(), now=now or repo.now())


def _ausfuehren(sitzung, konto, aktion):
    from . import endpunkte
    if aktion['art'] == 'bewertung' and aktion['platform'] in BRAUCHT_BROWSER:
        from . import browser as browseraktionen
        try:
            sitzung.proxy_url(konto['identity'])
            return browseraktionen.ausfuehren(sitzung, konto, aktion)
        except Exception as error:
            from ..net.accountproxy import AccountProxyError
            return Ergebnis(False, ART_PROXY if isinstance(error, AccountProxyError) else ART_FEHLER, str(error))
    try:
        proxy = sitzung.proxy_url(konto['identity'])
    except Exception as fehler:
        return Ergebnis(False, ART_PROXY, str(fehler))
    try:
        cookies = sitzung.cookies(konto['identity'], aktion['platform'])
    except (OSError, ValueError) as error:
        return Ergebnis(False, ART_ANMELDUNG, f'Die gespeicherte Anmeldung ist nicht lesbar: {error}')
    if not cookies:
        return Ergebnis(False, ART_ANMELDUNG, 'Keine gespeicherte Anmeldung.')
    aufruf = getattr(endpunkte, f"{aktion['platform']}_{aktion['art']}", None)
    if aufruf is None:
        return Ergebnis(False, ART_FEHLER, f"Für {aktion['platform']} gibt es „{aktion['art']}“ noch nicht.")
    try:
        return aufruf(cookies=cookies, proxy=proxy, ziel=aktion['ziel_uid'], video=aktion.get('video_uid'),
                      text=aktion.get('text'), grund=aktion.get('grund'), eltern=aktion.get('eltern_id'),
                      sterne=aktion.get('sterne'))
    except Exception as fehler:
        log.debug('Aktion fehlgeschlagen', exc_info=True)
        return Ergebnis(False, ART_UNKLAR, f'Der Plattformaufruf wurde unterbrochen ({type(fehler).__name__}); Ergebnis prüfen.')


def _fehler(db, aktion, konto, ergebnis, now=None):
    versuche = (aktion.get('versuche') or 0) + 1
    if ergebnis.art == ART_UNKLAR:
        _verschieben(db, aktion['id'], 'Ergebnis unklar; zuerst auf der Plattform prüfen. ' + ergebnis.meldung,
                     sekunden=0, versuche=versuche, status='laeuft')
        return
    if ergebnis.art in (ART_ZIEL, ART_FEHLER):
        status = 'uebersprungen' if versuche >= MAX_VERSUCHE or ergebnis.art == ART_ZIEL else 'offen'
        _verschieben(db, aktion['id'], ergebnis.meldung,
                     sekunden=0 if status == 'uebersprungen' else 600, versuche=versuche, status=status)
        return
    if ergebnis.art in rotation.COOLDOWN:
        rotation.mark_failure(db, konto['id'], ergebnis.meldung, ergebnis.art, now)
    _verschieben(db, aktion['id'], ergebnis.meldung, sekunden=_wartezeit(ergebnis.art, versuche), versuche=versuche,
                 status='uebersprungen' if versuche >= MAX_VERSUCHE else 'offen')


def _wartezeit(art, versuche):
    if art in rotation.COOLDOWN:
        _label, stunden = rotation.COOLDOWN[art]
        return int(stunden[min(versuche, len(stunden)) - 1] * 3600)
    return 600


def _verschieben(db, aktion_id, meldung, *, sekunden, versuche=None, status='offen'):
    faellig = repo.iso(repo.now() + datetime.timedelta(seconds=sekunden))
    if versuche is None:
        db.execute('UPDATE aktionen SET status=?, faellig_am=?, fehler=? WHERE id=?',
                   (status, faellig, str(meldung)[:500], aktion_id))
    else:
        db.execute('UPDATE aktionen SET status=?, faellig_am=?, versuche=?, fehler=? WHERE id=?',
                   (status, faellig, versuche, str(meldung)[:500], aktion_id))
    db.commit()


class _Runtime:
    """Cookies, Proxy, Browser für Aktionen."""

    def __init__(self, cfg):
        from .. import cli
        from ..net.cookies import CookieJar
        self.cfg = cfg
        self._jar = CookieJar
        self.im, _backend, _sessions = cli.make_runtime(cfg, need_browser=False)
        self._im_browser = None
        self._backend = None

    def cookies(self, identity, platform):
        return self._jar.load(identity, platform)

    def browser(self):
        if self._backend is None:
            from .. import cli
            self._im_browser, self._backend, self._sessions = cli.make_runtime(self.cfg, need_browser=True)
        return self._backend

    def im_browser(self):
        self.browser()
        return self._im_browser

    def proxy_url(self, identity):
        from .. import streuung
        fixed = streuung.proxy_url_fuer(self.cfg.db_path, identity)
        if fixed:
            return fixed
        country, mobile = self.im.stored(identity)
        person = self.im.get(identity, country or 'de', bool(mobile))
        proxy = self.im.proxy_for(person)
        if proxy is None:
            return None
        from ..net.accountproxy import set_entry, mask
        set_entry(self.cfg.db_path, identity, proxy.url)
        with Store(self.cfg.db_path) as store:
            row = store.db.execute('SELECT id FROM accounts WHERE identity=?', (identity,)).fetchone()
            if row:
                repo.set_account_proxy_ref(store.db, row[0], mask(proxy.url))
        return proxy.url

    def close(self):
        from ..cli import shutdown_runtime
        shutdown_runtime(self._backend, getattr(self, '_sessions', None))
