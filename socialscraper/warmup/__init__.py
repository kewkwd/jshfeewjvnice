"""Konten aufwärmen. Zwei Achsen: modus (Intensität) und umfang (Handlungen)."""
import datetime, logging, random, time

from .. import repo, rotation, streuung
from ..store import Store

log = logging.getLogger(__name__)

MODI = {
    'aus':      {'tage': 0, 'sitzungen_pro_tag': 0, 'minuten': (0, 0),  'schritte': 0},
    'schonend': {'tage': 7, 'sitzungen_pro_tag': 1, 'minuten': (2, 4),  'schritte': 6},
    'normal':   {'tage': 5, 'sitzungen_pro_tag': 2, 'minuten': (3, 7),  'schritte': 12},
    'zuegig':   {'tage': 3, 'sitzungen_pro_tag': 3, 'minuten': (5, 10), 'schritte': 20},
}
UMFANG = {
    'nur_schauen': ('scroll', 'video'),
    'liken':       ('scroll', 'video', 'like'),
    'aktiv':       ('scroll', 'video', 'like', 'kommentar'),
    'voll':        ('scroll', 'video', 'like', 'kommentar', 'folgen'),
}
SPALTEN = {'warmup_modus': 'TEXT', 'warmup_umfang': 'TEXT', 'warmup_until': 'TEXT',
           'warmup_last': 'TEXT', 'warmup_sitzungen': 'INTEGER DEFAULT 0',
           'phase': "TEXT DEFAULT 'web'", 'app_avd': 'TEXT', 'app_bis': 'TEXT', 'app_tage': 'INTEGER'}
APP_TAGE_WERTE = (3,)


def ensure_columns(db):
    vorhanden = {z[1] for z in db.execute('PRAGMA table_info(accounts)').fetchall()}
    for name, typ in SPALTEN.items():
        if name not in vorhanden:
            db.execute(f'ALTER TABLE accounts ADD COLUMN {name} {typ}')
    db.commit()


def app_tage_ziehen(db_path, *, rng=None):
    return streuung.naechster_wert(db_path, 'app_tage', APP_TAGE_WERTE, rng=rng)


def setzen(db_path, account_id, *, modus='normal', umfang='liken', tage=None, now=None,
           phase='web', app_avd=None, app_tage=None, rng=None):
    if phase not in ('web', 'app'):
        raise ValueError('Phase oder Dauer ungültig.')
    for dauer in (tage, app_tage):
        if dauer is not None and (type(dauer) is not int or dauer < 0):
            raise ValueError('Die Dauer muss eine nicht negative ganze Zahl sein.')
    if modus not in MODI:
        raise ValueError(f'Unbekannter Modus: {modus} (erlaubt: {", ".join(MODI)})')
    if umfang not in UMFANG:
        raise ValueError(f'Unbekannter Umfang: {umfang} (erlaubt: {", ".join(UMFANG)})')
    rng = rng or random
    if phase == 'app' and app_tage is None:
        app_tage = app_tage_ziehen(db_path, rng=rng)
    with Store(db_path) as store:
        db = store.db
        ensure_columns(db)
        account = repo.get_account(db, account_id)
        if account is None:
            raise ValueError('Konto nicht gefunden.')
        if phase == 'app' and account['platform'] != 'tiktok':
            raise ValueError('Die App-Phase ist nur für TikTok verfügbar.')
        plan = MODI[modus]
        dauer = plan['tage'] if tage is None else int(tage)
        bis = repo.iso((now or repo.now()) + datetime.timedelta(days=dauer)) if dauer else None
        app_bis = repo.iso((now or repo.now()) + datetime.timedelta(days=app_tage, hours=rng.uniform(0, 18))) \
            if phase == 'app' and app_tage is not None else None
        db.execute('UPDATE accounts SET warmup_modus=?, warmup_umfang=?, warmup_until=?, warmup_sitzungen=0, '
                   'phase=?, app_avd=?, app_bis=?, app_tage=? WHERE id=?',
                   (modus, umfang, bis, phase, app_avd, app_bis, app_tage, account_id))
        db.commit()
    return {'account_id': account_id, 'modus': modus, 'umfang': umfang, 'bis': bis,
            'phase': phase, 'app_bis': app_bis, 'app_tage': app_tage}


def zustand(db_path):
    with Store(db_path) as store:
        db = store.db
        ensure_columns(db)
        spalten = [z[1] for z in db.execute('PRAGMA table_info(accounts)').fetchall()]
        felder = [s for s in SPALTEN if s in spalten]
        cur = db.execute(f"SELECT id, platform, label, {', '.join(felder)} FROM accounts "
                         f"WHERE warmup_modus IS NOT NULL AND warmup_modus<>'aus' ORDER BY id")
        namen = [c[0] for c in cur.description]
        return [dict(zip(namen, z)) for z in cur.fetchall()]


def _app_phase(konto, now=None):
    # An elapsed date makes a handoff due; only confirmed web login changes phase.
    return konto.get('phase') == 'app'


def faellig(db_path, *, now=None):
    from .sitzung import PLATTFORMEN
    jetzt = now or repo.now()
    with Store(db_path) as store:
        db = store.db
        ensure_columns(db)
        cur = db.execute("SELECT * FROM accounts WHERE warmup_modus IS NOT NULL AND warmup_modus<>'aus' "
                         "AND warmup_until IS NOT NULL AND warmup_until>? ORDER BY COALESCE(warmup_last, '')",
                         (repo.iso(jetzt),))
        namen = [c[0] for c in cur.description]
        ausgabe = []
        for zeile in cur.fetchall():
            konto = dict(zip(namen, zeile))
            if konto['platform'] not in PLATTFORMEN or konto.get('warmup_modus') not in MODI or konto.get('warmup_umfang') not in UMFANG:
                continue
            plan = MODI.get(konto.get('warmup_modus') or 'normal') or MODI['normal']
            if not plan['sitzungen_pro_tag']:
                continue
            abstand = datetime.timedelta(hours=24 / plan['sitzungen_pro_tag'])
            try:
                letzte = repo.parse(konto.get('warmup_last')) if konto.get('warmup_last') else None
                phase_app = _app_phase(konto, jetzt)
                if repo.parse(konto['warmup_until']) <= jetzt:
                    continue
            except (ValueError, TypeError):
                log.warning('Ungültiger Aufwärm-Zeitplan für Konto %s.', konto['id'])
                continue
            if letzte and jetzt - letzte < abstand + datetime.timedelta(
                    minutes=streuung.versatz(konto['identity'], spanne=90)):
                continue
            if not streuung.im_fenster(konto, jetzt):
                continue
            login = None
            if phase_app and konto['platform'] == 'tiktok':
                from .. import accountvault
                saved = accountvault.get(db_path, konto['identity'])
                login = bool(saved.get('email') and saved.get('passwort'))
            if rotation.account_state(konto, jetzt, rotation.root_of(db), login_available=login)['state'] != 'bereit':
                continue
            ausgabe.append(konto)
        return ausgabe


def _wechsel_faellige(cfg, db_path, *, konto_id=None, now=None, melden=log.info, stop=None):
    from ..signup import tiktok
    with Store(db_path) as store:
        ensure_columns(store.db)
        sql = "SELECT * FROM accounts WHERE platform='tiktok' AND phase='app' AND app_bis IS NOT NULL AND app_bis<=?"
        args = [repo.iso(now or repo.now())]
        if konto_id is not None:
            sql += ' AND id=?'
            args.append(konto_id)
        cur = store.db.execute(sql, args)
        namen = [c[0] for c in cur.description]
        konten = [dict(zip(namen, z)) for z in cur.fetchall()]
    for konto in konten:
        if stop is not None and stop():
            return
        melden(f'„{konto["label"]}“ – App-Phase vorbei, stelle auf Browser um.')
        try:
            if not tiktok.web_anmelden(cfg, konto, melden=melden, stop=stop):
                melden('Web-Anmeldung offen – bleibt in der App-Phase.')
                continue
        except Exception as fehler:
            if stop is not None and stop():
                return
            melden(f'Umstellung fehlgeschlagen: {fehler}')
            continue
        if stop is not None and stop():
            return
        with Store(db_path) as store:
            store.db.execute("UPDATE accounts SET phase='web' WHERE id=?", (konto['id'],))
            store.db.commit()
        melden(f'„{konto["label"]}“ läuft jetzt über den Browser.')


from ..operation import exclusive, check_cancelled

@exclusive
def run(db_path, *, konto_id=None, cfg=None, trocken=False, melden=log.info, stop=None, now=None):
    from . import sitzung
    if cfg is None:
        from ..config import load
        cfg = load()
    bericht = {'sitzungen': 0, 'konten': 0, 'fehler': 0, 'abgebrochen': False}
    from ..net import proxymode
    proxymode.require_no_anon(cfg, 'Aufwärmen')
    if stop is not None and stop():
        bericht['abgebrochen'] = True
        return bericht
    if konto_id is not None:
        with Store(db_path) as store:
            if repo.get_account(store.db, konto_id) is None:
                raise ValueError('Konto nicht gefunden.')
    if not trocken:
        _wechsel_faellige(cfg, db_path, konto_id=konto_id, now=now, melden=melden, stop=stop)
    if stop is not None and stop():
        bericht['abgebrochen'] = True
        return bericht
    offen = [k for k in faellig(db_path, now=now) if konto_id in (None, k['id'])]
    if not offen:
        melden('Kein Konto ist zum Aufwärmen fällig.')
        return bericht

    for konto in offen:
            if stop is not None and stop():
                bericht['abgebrochen'] = True
                break
            plan, erlaubt = MODI[konto['warmup_modus']], UMFANG[konto['warmup_umfang']]
            melden(f"Wärme „{konto['label']}“ auf ({konto['warmup_modus']}/{konto['warmup_umfang']}).")
            if trocken:
                bericht['konten'] += 1
                continue
            try:
                if _app_phase(konto, now):
                    app_avd = konto.get('app_avd')
                    if not isinstance(app_avd, str) or not app_avd.strip():
                        raise ValueError('Für dieses Konto fehlt das gespeicherte Android-Gerät.')
                    from ..android import warmup_app
                    from ..android.geraet import vorbereiten, mit_geraet
                    from ..android import tiktok_app
                    from ..signup import Ctx
                    from .. import accountvault
                    from ..mailbox import build as mailbox_build
                    from ..net.proxy import Proxy
                    check_cancelled(stop)
                    try:
                        proxy = streuung.proxy_url_fuer(db_path, konto['identity'])
                        mailbox_proxy = Proxy.parse(proxy)
                    except (ValueError, TypeError):
                        raise ValueError('Für dieses Konto fehlt ein gültiger fester Proxy; App-Aufwärmen bleibt gesperrt.') from None
                    check_cancelled(stop)
                    saved = accountvault.get(db_path, konto['identity'])
                    if not saved.get('email') or not saved.get('passwort'):
                        raise ValueError('Für die App-Anmeldung fehlen gespeicherte Zugangsdaten.')
                    expected_username = accountvault.normalize_tiktok_username(saved.get('tiktok_username'), required=True)
                    context = Ctx(platform='tiktok', email=saved['email'], password=saved['passwort'],
                                  username=konto['identity'], mode='assisted', country=konto.get('country') or 'de',
                                  mobile=True, timeout=300, backend=None, identity=konto['identity'],
                                  melden=melden, stop=stop,
                                  tiktok_username=expected_username)
                    context.check_cancelled()
                    if saved.get('email_passwort'):
                        context.box = mailbox_build({'benutzer': saved['email'], 'passwort': saved['email_passwort']},
                                                    proxy=mailbox_proxy)
                    else:
                        pool = next((e for e in accountvault.postfaecher(db_path) if e.get('adresse', '').casefold() == saved['email'].casefold()), None)
                        context.check_cancelled()
                        if pool:
                            context.box = mailbox_build(accountvault.als_postfach(pool), proxy=mailbox_proxy)
                    context.check_cancelled()
                    with mit_geraet(app_avd, proxy=proxy, stop_requested=stop) as adb:
                        context.check_cancelled()
                        vorbereiten(adb, proxy=proxy)
                        context.check_cancelled()
                        if not tiktok_app.anmelden(adb, context):
                            raise ValueError('Die App-Anmeldung wurde nicht bestätigt.')
                        context.check_cancelled()
                        schritte = warmup_app.sitzung(adb, konto, plan=plan, erlaubt=erlaubt,
                                                      melden=melden, stop=stop)
                else:
                    schritte = sitzung.laufen(cfg, konto, plan=plan, erlaubt=erlaubt, melden=melden, stop=stop)
            except Exception as fehler:
                if stop is not None and stop():
                    bericht['abgebrochen'] = True
                    break
                log.debug('Aufwärmen fehlgeschlagen', exc_info=True)
                melden(f"Aufwärmen von „{konto['label']}“ fehlgeschlagen: {fehler}")
                bericht['fehler'] += 1
                continue
            if stop is not None and stop():
                bericht['abgebrochen'] = True
                break
            if not schritte:
                melden(f"Für „{konto['label']}“ wurde keine Handlung bestätigt.")
                bericht['fehler'] += 1
                continue
            with Store(db_path) as store:
                jetzt = repo.iso(repo.now())
                store.db.execute('UPDATE accounts SET warmup_last=?, warmup_sitzungen=COALESCE(warmup_sitzungen,0)+1,'
                                 ' last_used_at=? WHERE id=?', (jetzt, jetzt, konto['id']))
                store.db.commit()
                rotation.mark_used(store.db, konto['id'], now)
            bericht['sitzungen'] += 1
            bericht['konten'] += 1
            melden(f"„{konto['label']}“ aufgewärmt ({schritte} Handlungen).")
    return bericht
