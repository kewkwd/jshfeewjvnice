"""Streuung gegen Cluster-Bildung. Kernregel: ein Konto = eine Identität."""
import datetime, json, logging, random, re
from pathlib import Path

from . import repo
from .net.accountproxy import mask
from .store import Store

log = logging.getLogger(__name__)

GRENZEN = {'ip': 1, 'fingerabdruck': 1, 'mail_domaene': 3, 'namensschema': 4, 'geraet': 1}
ABSTAND_MINUTEN = {'ip': 20, 'plattform': 3}
FENSTER = (8, 23)
TAKT_PRO_MINUTE = 4
SPALTEN = {'fenster_von': 'INTEGER', 'fenster_bis': 'INTEGER', 'streuung_offset': 'INTEGER DEFAULT 0'}
NAMENSSCHEMATA = ('{w}{z}', '{w}{w}{z}', '{w}_{w}', '{w}.{w}{z}', 'x{w}{z}', '{w}{z}{w}')
WOERTER = ('blick', 'wolke', 'feder', 'stein', 'licht', 'nord', 'fluss', 'hafen', 'mond', 'wald',
           'stern', 'brise', 'anker', 'kiefer', 'morgen', 'silber', 'wander', 'quelle', 'donner',
           'wiese', 'klippe', 'funke', 'nebel', 'tau')


class StreuungError(RuntimeError):
    pass


def ensure_columns(db):
    vorhanden = {z[1] for z in db.execute('PRAGMA table_info(accounts)').fetchall()}
    for name, typ in SPALTEN.items():
        if name not in vorhanden:
            db.execute(f'ALTER TABLE accounts ADD COLUMN {name} {typ}')
    db.commit()


def proxy_je_konto(db_path):
    from .net.accountproxy import load
    return load(db_path)


def proxy_url_fuer(db_path, identity):
    from .net.accountproxy import Resolver
    from urllib.parse import quote
    proxy = Resolver(db_path).proxy_for(identity)
    if proxy is None:
        return None
    host = f'[{proxy.host}]' if ':' in proxy.host else proxy.host
    auth = ''
    if getattr(proxy, 'user', None):
        auth = quote(proxy.user, safe='') + ':' + quote(proxy.password or '', safe='') + '@'
    return f'{proxy.scheme}://{auth}{host}:{proxy.port}'


def belegte_proxies(db_path):
    return set(proxy_je_konto(db_path).values())


def freier_proxy(db_path, cfg=None):
    belegt = belegte_proxies(db_path)
    kandidaten = _lies(getattr(cfg, 'proxy_file', None)) or _lies(Path(db_path).parent / 'proxies.txt')
    for eintrag in kandidaten:
        if eintrag not in belegt:
            return eintrag
    return None


def _lies(pfad):
    try:
        text = Path(pfad).read_text(encoding='utf-8-sig')
    except (OSError, TypeError):
        return []
    return [z.strip() for z in text.splitlines() if z.strip() and not z.strip().startswith('#')]


def _tresor(db_path):
    from . import accountvault
    return accountvault.load(db_path)


def mail_domaene(db_path, identity, *, tresor=None):
    adresse = ((tresor or _tresor(db_path)).get('konten') or {}).get(identity, {}).get('email') or ''
    return adresse.rpartition('@')[2].lower()


def namensschema(identity):
    text = re.sub(r'^[a-z]+_', '', str(identity))
    if '_' in text:
        return '{w}_{w}'
    if '.' in text:
        return '{w}.{w}{z}'
    if text.startswith('x'):
        return 'x{w}{z}'
    if re.fullmatch(r'[a-z]+\d+', text):
        return '{w}{z}'
    if re.fullmatch(r'[a-z]{6,}', text):
        return '{w}{w}{z}'
    return '{w}{z}'


def _schema_zaehler(db_path):
    zaehler = {}
    with Store(db_path) as store:
        for (identity,) in store.db.execute('SELECT identity FROM accounts').fetchall():
            zaehler[namensschema(identity)] = zaehler.get(namensschema(identity), 0) + 1
    return zaehler


def _vorhandene(db_path):
    with Store(db_path) as store:
        return {z[0] for z in store.db.execute('SELECT identity FROM accounts').fetchall()}


def naechster_name(db_path, platform, *, rng=None):
    rng = rng or random
    zaehler = _schema_zaehler(db_path)
    schema = min(NAMENSSCHEMATA, key=lambda s: (zaehler.get(s, 0), NAMENSSCHEMATA.index(s)))
    vorhanden = _vorhandene(db_path)
    for _ in range(50):
        name = f'{platform}_{schema.format(w=rng.choice(WOERTER), z=rng.randint(10, 9999))}'[:30]
        if name not in vorhanden:
            return name
    return f'{platform}_{rng.randint(10 ** 5, 10 ** 6)}'


def naechster_wert(db_path, spalte, werte, *, rng=None):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', spalte):
        raise ValueError('Ungültige Spalte.')
    rng, werte = rng or random, list(werte)
    if not werte:
        raise ValueError('Es fehlen Auswahlwerte.')
    zaehler = {}
    with Store(db_path) as store:
        spalten = {z[1] for z in store.db.execute('PRAGMA table_info(accounts)').fetchall()}
        if spalte not in spalten:
            return rng.choice(werte)
        for (wert,) in store.db.execute(f'SELECT {spalte} FROM accounts'):
            zaehler[wert] = zaehler.get(wert, 0) + 1
    kleinste = min(zaehler.get(w, 0) for w in werte)
    return rng.choice([w for w in werte if zaehler.get(w, 0) == kleinste])


def pruefe_neues_konto(db_path, *, identity, proxy=None, email=None, tresor=None):
    warnungen, proxy_map = [], proxy_je_konto(db_path)
    if proxy:
        gleiche = [k for k, v in proxy_map.items() if v == proxy and k != identity]
        if len(gleiche) >= GRENZEN['ip']:
            warnungen.append(('hoch', f'Die IP ist schon von {", ".join(gleiche)} benutzt.'))
    if email:
        domaene = email.rpartition('@')[2].lower()
        gleich = [k for k in proxy_map if mail_domaene(db_path, k, tresor=tresor) == domaene]
        if len(gleich) >= GRENZEN['mail_domaene']:
            warnungen.append(('mittel', f'Schon {len(gleich)} Konten auf @{domaene}.'))
    if _schema_zaehler(db_path).get(namensschema(identity), 0) >= GRENZEN['namensschema']:
        warnungen.append(('niedrig', 'Das Namensschema ist mehrfach vergeben.'))
    return warnungen


def _gruppen(paare):
    gruppen = {}
    for schluessel, wert in paare.items():
        gruppen.setdefault(wert, []).append(schluessel)
    return gruppen


def audit(db_path):
    befunde, proxy_map, tresor = [], proxy_je_konto(db_path), _tresor(db_path)
    identities = _vorhandene(db_path) | set(proxy_map)
    for proxy, konten in _gruppen(proxy_map).items():
        if len(konten) > GRENZEN['ip']:
            befunde.append({'art': 'ip', 'schwere': 'hoch', 'wert': mask(proxy), 'konten': sorted(konten),
                            'hinweis': f'{len(konten)} Konten auf einer IP – jede Sperre trifft alle.'})
    for domaene, konten in _gruppen({k: mail_domaene(db_path, k, tresor=tresor) for k in identities}).items():
        if domaene and len(konten) > GRENZEN['mail_domaene']:
            befunde.append({'art': 'mail_domaene', 'schwere': 'mittel', 'wert': f'@{domaene}',
                            'konten': sorted(konten), 'hinweis': f'{len(konten)} Konten auf einem Anbieter.'})
    for schema, anzahl in _schema_zaehler(db_path).items():
        if anzahl > GRENZEN['namensschema']:
            befunde.append({'art': 'namensschema', 'schwere': 'niedrig', 'wert': schema, 'konten': [],
                            'hinweis': f'{anzahl} Konten nach demselben Muster.'})
    with Store(db_path) as store:
        zeilen = store.db.execute('SELECT identity, platform, created_at FROM accounts ORDER BY created_at').fetchall()
    previous = {}
    for jetzt in zeilen:
        vorher = previous.get(jetzt[1])
        previous[jetzt[1]] = jetzt
        if vorher is None:
            continue
        try:
            abstand = (repo.parse(jetzt[2]) - repo.parse(vorher[2])).total_seconds() / 60
        except Exception:
            continue
        if abstand < ABSTAND_MINUTEN['plattform']:
            befunde.append({'art': 'zeit', 'schwere': 'mittel', 'wert': f'{abstand:.0f} min',
                            'konten': [vorher[0], jetzt[0]], 'hinweis': 'Zwei Konten fast gleichzeitig.'})
    return befunde


def bann_verdacht(db_path, identity):
    proxy_map = proxy_je_konto(db_path)
    mein = proxy_map.get(identity)
    mitglieder = sorted(k for k, v in proxy_map.items() if v == mein and k != identity) if mein else []
    from .net.accountproxy import mask
    return {'identity': identity, 'proxy': mask(mein), 'mitglieder': mitglieder,
            'warnung': (f'{len(mitglieder)} Konto/Konten teilen die IP.' if mitglieder
                        else 'Kein Kontakt zu anderen Konten.')}


def versatz(identity, *, spanne=45):
    return int.from_bytes(str(identity).encode('utf-8'), 'little') % spanne


def setze_fenster(db_path, account_id, *, von=None, bis=None, rng=None):
    rng = rng or random
    von = FENSTER[0] + rng.randint(0, 2) if von is None else int(von)
    bis = FENSTER[1] - rng.randint(0, 2) if bis is None else int(bis)
    if not 0 <= von <= 23 or not 0 <= bis <= 24 or von == bis:
        raise ValueError('Aktivitätszeiten müssen zwischen 0 und 24 Uhr liegen und ein nichtleeres Fenster bilden.')
    with Store(db_path) as store:
        db = store.db
        ensure_columns(db)
        if repo.get_account(db, account_id) is None:
            raise ValueError('Konto nicht gefunden.')
        db.execute('UPDATE accounts SET fenster_von=?, fenster_bis=?, streuung_offset=? WHERE id=?',
                   (von, bis, rng.randint(0, 90), account_id))
        db.commit()
    return {'account_id': account_id, 'von': von, 'bis': bis}


def im_fenster(konto, now=None):
    jetzt, von, bis = (now or repo.now()).astimezone(), konto.get('fenster_von'), konto.get('fenster_bis')
    if von is None or bis is None:
        return True
    return von <= jetzt.hour < bis if von <= bis else (jetzt.hour >= von or jetzt.hour < bis)


def takt_wartezeit(db_path, *, takt_pro_minute=TAKT_PRO_MINUTE, now=None):
    if not takt_pro_minute:
        return 0.0
    with Store(db_path) as store:
        stamp = now or repo.now()
        rows = store.db.execute('SELECT started_at FROM action_attempts WHERE started_at>? '
                                'ORDER BY started_at DESC,id DESC LIMIT ?',
                                (repo.iso(stamp-datetime.timedelta(seconds=60)),int(takt_pro_minute))).fetchall()
    if len(rows) < takt_pro_minute:
        return 0.0
    return max(0.0,(repo.parse(rows[-1][0])+datetime.timedelta(seconds=60)-stamp).total_seconds())


def streuen(db_path, *, spanne_minuten=240, platform=None, now=None, rng=None):
    if isinstance(spanne_minuten, bool) or not isinstance(spanne_minuten, (int, float)) or not 0 < spanne_minuten <= 10080:
        raise ValueError('Die Zeitspanne muss größer als null und höchstens sieben Tage sein.')
    rng, jetzt = rng or random, now or repo.now()
    with Store(db_path) as store:
        db = store.db
        from .aktionen import ensure_schema
        ensure_schema(db)
        bedingungen, werte = ["status='offen'"], []
        if platform:
            bedingungen.append('platform=?')
            werte.append(platform)
        ids = [z[0] for z in db.execute(f"SELECT id FROM aktionen WHERE {' AND '.join(bedingungen)}", werte)]
        rng.shuffle(ids)
        for i, aktion_id in enumerate(ids):
            anteil = (i + rng.random()) / max(1, len(ids))
            db.execute('UPDATE aktionen SET faellig_am=? WHERE id=?',
                       (repo.iso(jetzt + datetime.timedelta(minutes=anteil * spanne_minuten)), aktion_id))
        db.commit()
    return len(ids)


def wartezeit_bis_registrierung(db_path, *, proxy=None, platform=None, now=None):
    jetzt, proxy_map, warten = now or repo.now(), proxy_je_konto(db_path), 0.0
    with Store(db_path) as store:
        for ident, account_platform, created in store.db.execute('SELECT identity, platform, created_at FROM accounts').fetchall():
            try:
                alter = (jetzt - repo.parse(created)).total_seconds() / 60
            except Exception:
                continue
            if proxy and proxy_map.get(ident) == proxy:
                warten = max(warten, (ABSTAND_MINUTEN['ip'] - alter) * 60)
            if platform and account_platform == platform:
                warten = max(warten, (ABSTAND_MINUTEN['plattform'] - alter) * 60)
    return max(0.0, warten)
