"""Fehlerübersicht in einfacher Sprache (ohne Tk): sammelt, was zuletzt schiefging, und sagt, was zu tun ist.

Quellen: Läufe (`runs.error`), Kanäle (`channel_items`, Pause), Konten (Abkühlung), Hintergrunddienst (`data/daemon.log`).
Gelesen wird nur (schreibgeschützt). Meldungen enthalten nie Geheimnisse: Proxy-Passwörter werden gefiltert.
"""
import datetime
import pathlib
import re
import sqlite3
import zlib

from . import autologic as al
from . import repo

WINDOW_DAYS = 7
MAX_ITEMS = 60

# Muster -> (Überschrift, Was tun?, Ziel). Ziel = (Tab, Unterziel) oder None. Die erste Übereinstimmung gilt.
RULES = [
    (r'registrierung fehlgeschlagen|fehler beim registrierungseinstieg', 'Der Anbieter hat den Registrierungsversuch abgelehnt; die genaue Ursache ist unbekannt.',
     'Prüfe den angezeigten Registrierungsschritt. Starte keine wiederholten Versuche ohne neue Erkenntnisse.', ('Postfächer', None)),
    (r'einstieg.*(?:nicht abgeschlossen|nicht rechtzeitig|nicht gefunden)|registrierungslink wurde nicht erreicht',
     'Der vorgesehene Weg zur Registrierung wurde nicht abgeschlossen.',
     'Prüfe die letzte Seite und offene Bestätigungen. Kein direkter Ersatzaufruf und keine automatische Wiederholung.', ('Arbeitsregeln', None)),
    (r'captcha|menschentest|prove your humanity|sicherheitsabfrage|bist du ein mensch', 'Die Plattform wollte einen Menschentest (CAPTCHA).',
     'Bestätige die Abfrage selbst im sichtbaren Browserfenster über den eingerichteten Proxy. Beachte eine bestehende Kontopause.', ('Konten', None)),
    (r'blocked by network|internetverbindung gesperrt|verbindung.*gesperrt', 'Die Plattform hat den Abruf durch eine Sicherheitsprüfung gesperrt.',
     'Die Ursache ist nicht eindeutig. Prüfe Anmeldung und Sitzung; behalte die eingerichtete Proxy-Verbindung bei.', ('Einstellungen', 'proxys')),
    # Match explicit errors before words contained only in their suggested fix
    # (e.g. "Proxy prüfen" or "Anmeldung prüfen").
    (r'zertifikat|certificate|sslerror|err_cert', 'Die Zertifikatsprüfung ist fehlgeschlagen.',
     'Prüfe Systemzeit und Zertifikatskonfiguration des Servers beziehungsweise Proxys.', ('Einstellungen', 'proxys')),
    (r'\b(?:403|401)\b|zugriff verweigert', 'Die Plattform verweigert den Zugriff auf diesen Abruf.',
     'Der Statuscode allein nennt keine genaue Ursache. Prüfe Anmeldung, Berechtigung und Zieladresse.', ('Konten', None)),
    (r'konto.*gesperrt|eingeschränkt|account.*suspend', 'Die Plattform hat das Konto gesperrt oder eingeschränkt.',
     'Kläre die genaue Meldung und den letzten Schritt gemeinsam mit dem Assistenten. Bei bestätigter Sperre den offiziellen Wiederherstellungsweg prüfen.', ('Konten', None)),
    (r'anmeld|login|abgelaufen|sitzung', 'Die Anmeldung fehlt oder ist abgelaufen.',
     'Melde das Konto im Tab „Konten“ neu an.', ('Konten', None)),
    (r'proxy|tor\b|konten sind gesperrt|proxy-modus', 'Die gewählte Verbindung (Proxy-Modus) passt nicht oder ist nicht erreichbar.',
     'Prüfe den eingerichteten Proxy beziehungsweise Tor. Ohne funktionierende Route keinen Abruf starten.', ('Einstellungen', 'proxys')),
    (r'429|zu viele anfragen', 'Die Plattform bremst aus („zu viele Anfragen“).',
     'Warte eine Weile. Stelle das Tempo langsamer oder hole weniger Kommentare pro Video.', None),
    (r'zeitüberschreitung|timeout|nicht rechtzeitig', 'Der Server hat nicht rechtzeitig geantwortet.',
     'Prüfe deine Internetverbindung. Der nächste Lauf versucht es von allein wieder.', None),
    (r'dns|keine verbindung|connection|netz', 'Es gab keine Verbindung zum Server.',
     'Prüfe deine Internetverbindung.', None),
    (r'privat|gelöscht|nicht verfügbar|regional', 'Das Video ist nicht abrufbar (privat, gelöscht oder in deinem Land gesperrt).',
     'Nichts tun: Das Video wird übersprungen.', None),
    (r'unterbrochen|programm beendet', 'Der Lauf wurde unterbrochen (Programm oder PC wurde beendet).',
     'Nichts tun: Der nächste Lauf macht weiter.', None),
    (r'datenbank.*(gesperrt|beschäftigt|locked)', 'Die Datenbank war gerade belegt.',
     'Nichts tun. Versuche es in ein paar Sekunden noch einmal.', None),
    (r'browser|chrome|patchright', 'Der Browser hat nicht mitgespielt.',
     'Prüfe unter Einstellungen → System, ob Chrome gefunden wird.', ('Einstellungen', 'system')),
]

_SECRET = re.compile(r'(?i)((?:pass(?:word|wort)?|token|key|schlüssel|authorization|bearer|secret)\s*[=:]\s*)\S+')
_URLCRED = re.compile(r'(\w+://)[^/\s:@]+:[^/\s@]+@')
_SECRET_NAMES = r'(?:password|passwort|email_passwort|token|access_token|refresh_token|api_key|key|secret|client_secret|authorization|totp_secret)'
_QUOTED_SECRET = re.compile(rf'''(?is)((?:"{_SECRET_NAMES}"|'{_SECRET_NAMES}'|\b{_SECRET_NAMES})\s*[=:]\s*)(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$))''')


def scrub(text):
    """Geheimnisse und Zugangsdaten aus einer Meldung entfernen; Länge begrenzen."""
    text = _URLCRED.sub(r'\1', str(text or ''))
    text = re.sub(r'(?i)(\bbearer\s+)\S+', r'\1[entfernt]', text)
    text = _QUOTED_SECRET.sub(r'\1"[entfernt]"', text)
    text = _SECRET.sub(r'\1[entfernt]', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:300]


def explain(error_text):
    """(Überschrift, Was tun?, Ziel) für eine Fehlermeldung. Kennen wir sie nicht, bleibt der bereinigte Text stehen."""
    clean = scrub(error_text)
    low = clean.lower()
    for pattern, head, todo, goto in RULES:
        if re.search(pattern, low):
            return head, todo, goto
    if not clean:
        return 'Es ist etwas schiefgegangen.', 'Schau in das Protokoll („Technische Details“).', None
    return clean, 'Mehr steht im Protokoll („Technische Details“). Der nächste Lauf versucht es von allein wieder.', None


def is_known(error_text):
    """Erkennt `explain` diese Meldung (sonst bleibt nur der bereinigte Text stehen)?"""
    low = scrub(error_text).lower()
    return any(re.search(pattern, low) for pattern, _h, _t, _g in RULES)


def from_job_results(results, when):
    """Session-only failures for commands that have no database run record."""
    items = []
    for index, (label, code, lines) in enumerate(results):
        if code == 0: continue
        # Only classify explicit error lines. Progress hints may mention CAPTCHA,
        # proxy or login without being the failure that ended the command.
        error = next((line for line in reversed(lines[-100:])
                      if re.search(r'\b(?:ERROR|CRITICAL|FinalError|RuntimeError|ValueError)\b', line)), '')
        if is_known(error):
            cause, todo, goto = explain(error)
        else:
            cause, todo, goto = ('Der Auftrag wurde mit einem Fehler beendet.',
                'Öffne das Protokoll unter Details, um die genaue Meldung zu lesen.', None)
        items.append({'time': when, 'kind': 'job', 'what': scrub(label) + ' – fehlgeschlagen',
                      'cause': cause, 'todo': todo, 'goto': goto,
                      'key': f'job:{when.isoformat()}:{index}'})
    return items


def with_session_failures(items, failures):
    since = repo.now() - datetime.timedelta(days=WINDOW_DAYS)
    combined = {item['key']: item for item in [*items, *failures] if item['time'] >= since}
    return sorted(combined.values(), key=lambda item: item['time'], reverse=True)[:MAX_ITEMS]


def _parse(text):
    return al.parse_utc(text)


def _target_name(row):
    if row.get('target_kind'):
        return al.target_title({'kind': row['target_kind'], 'platform': row.get('target_platform') or '', 'value': row.get('target_value') or ''})
    return al.deleted_title(row.get('label')) if row.get('label') else 'Abruf von Hand'


def from_runs(db, since, limit=40):
    out = []
    for row in repo.list_runs(db, 200):
        if row['status'] not in ('failed', 'partial'):
            continue
        when = _parse(row.get('finished_at') or row.get('started_at'))
        if when is None or when < since:
            continue
        if row['status'] == 'partial' and not row.get('error'):
            continue
        head, todo, goto = explain(row.get('error') or '')
        what = _target_name(row)
        out.append({'time': when, 'kind': 'run', 'what': what + (' – teilweise geklappt' if row['status'] == 'partial' else ' – fehlgeschlagen'),
                    'cause': head, 'todo': todo, 'goto': goto or ('Automatik', None), 'key': f'run:{row["id"]}', 'target_id': row.get('target_id')})
        if len(out) >= limit:
            break
    return out


def from_channels(db, since, now):
    out = []
    try:
        channels = repo.list_channels(db)
    except sqlite3.OperationalError:
        return out
    for t in channels:
        name = al.target_title(t)
        if t.get('backfill_state') == 'paused' and t.get('enabled'):
            until = _parse(t.get('pause_until'))
            from .rotation import fmt_when
            when = fmt_when(until, now) if until else None
            reason = t.get('pause_reason') or 'einem Fehler'
            head, todo, goto = explain(reason)
            out.append({'time': _parse(t.get('last_listed_at')) or now, 'kind': 'channel',
                        'what': f'{name} macht Pause' + (f' bis {when}' if when else ''),
                        'cause': f'Grund: {reason}.', 'todo': 'Nichts tun: Der Kanal macht von allein weiter.', 'goto': ('Automatik', f'channel:{t["id"]}'),
                        'key': f'chanpause:{t["id"]}', 'target_id': t['id']})
        failed = (t.get('progress') or {}).get('failed', 0)
        if failed:
            row = db.execute("SELECT last_error, tried_at FROM channel_items WHERE target_id=? AND status='failed' "
                             "ORDER BY COALESCE(tried_at, '') DESC LIMIT 1", (t['id'],)).fetchone()
            err = row[0] if row else ''
            head, todo, goto = explain(err or '')
            when = _parse(row[1]) if row and row[1] else None
            if when is not None and when < since:
                continue
            out.append({'time': when or now, 'kind': 'channel',
                        'what': f'{name}: {failed} {"Video" if failed == 1 else "Videos"} fehlgeschlagen',
                        'cause': head, 'todo': 'Mit „Fehlgeschlagene erneut“ im Tab „Automatik“ kommen sie noch einmal dran.'
                                               + (' ' + todo if is_known(err or '') else ''),
                        'goto': ('Automatik', f'channel:{t["id"]}'), 'key': f'chanfail:{t["id"]}', 'target_id': t['id']})
    return out


def from_accounts(db, now, root):
    out = []
    from .rotation import account_state, platform_name
    for acc in repo.list_accounts(db):
        state = account_state(acc, now, root)
        if state['state'] != 'abkuehlung':
            continue
        until = state['until']
        reason = acc.get('cooldown_reason') or 'einem Fehler'
        out.append({'time': _parse(acc.get('last_used_at')) or now, 'kind': 'account',
                    'what': f'{platform_name(acc["platform"])}-Konto „{acc["label"]}“ macht Pause',
                    'cause': f'{state["text"]}.', 'todo': 'Nichts tun: Das Konto wird in der Pause nicht benutzt und ist danach wieder dran.',
                    'goto': ('Konten', f'account:{acc["id"]}'), 'key': f'cool:{acc["id"]}:{acc.get("cooldown_until")}', 'until': until})
    return out


_LOG_LINE = re.compile(r'^(?:(?P<d>\d{4}-\d\d-\d\d)[ T])?(?P<t>\d\d:\d\d:\d\d)(?:[,.]\d{1,6})?'
                       r'\s+(?P<lvl>ERROR|CRITICAL)\s+[\w.]+\s*\|\s*(?P<msg>.*)$')   # daemon.log: „2026-10-13 20:08:07,481 ERROR …“


def from_daemon_log(path, now, since, max_bytes=120_000):
    """Fehlerzeilen des Hintergrunddienstes. Die Datei hat nur Uhrzeiten; ein Datum wird aus „heute oder gestern“ erschlossen."""
    path = pathlib.Path(path)
    out = []
    try:
        stat = path.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc)
        if mtime < since:
            return out
        with path.open('rb') as handle:
            handle.seek(max(0, stat.st_size - max_bytes))
            text = handle.read().decode('utf-8', 'replace')
    except OSError:
        return out
    local_now = now.astimezone()
    seen = set()
    for line in reversed(text.splitlines()):
        m = _LOG_LINE.match(line.strip())
        if not m:
            continue
        msg = scrub(m.group('msg'))
        if msg in seen:
            continue
        seen.add(msg)
        hh, mm, ss = (int(x) for x in m.group('t').split(':'))
        try:   # naive Ortszeit -> astimezone nimmt den Versatz, der an genau diesem Tag gilt (Zeitumstellung)
            if m.group('d'):   # Datei des Dienstes: das Datum steht in der Zeile
                y, mo, d = (int(x) for x in m.group('d').split('-'))
                moment = datetime.datetime(y, mo, d, hh, mm, ss).astimezone(datetime.timezone.utc)
            else:              # nur eine Uhrzeit: heute oder gestern
                day = local_now.date()
                moment = datetime.datetime.combine(day, datetime.time(hh, mm, ss)).astimezone(datetime.timezone.utc)
                if moment > now + datetime.timedelta(minutes=5):
                    moment = datetime.datetime.combine(day - datetime.timedelta(days=1),
                                                       datetime.time(hh, mm, ss)).astimezone(datetime.timezone.utc)
        except (ValueError, OverflowError):
            continue
        if moment < since:
            continue
        head, todo, goto = explain(msg)
        out.append({'time': moment, 'kind': 'service', 'what': 'Hintergrunddienst', 'cause': head, 'todo': todo,
                    'goto': goto or ('Automatik', None), 'key': 'log:%s:%d' % (m.group('t'), zlib.crc32(msg.encode('utf-8')) & 0xffff)})
        if len(out) >= 8:
            break
    return out


def collect(root, db_path, now=None, days=WINDOW_DAYS):
    """Alle Probleme der letzten Tage, neueste zuerst. Fehlt die Datenbank, bleibt die Liste leer (kein Fehler)."""
    now = now or repo.now()
    since = now - datetime.timedelta(days=days)
    items = []
    try:
        with al._read(db_path) as db:
            if db is not None:
                for part in (lambda: from_runs(db, since), lambda: from_channels(db, since, now), lambda: from_accounts(db, now, root)):
                    try:
                        items += part()
                    except sqlite3.Error:
                        continue
    except sqlite3.Error:
        pass
    items += from_daemon_log(pathlib.Path(root) / 'data' / 'daemon.log', now, since)
    for item in items:
        for key in ('what', 'cause', 'todo'):
            if key in item:
                item[key] = scrub(item[key])
    items.sort(key=lambda i: i['time'], reverse=True)
    return items[:MAX_ITEMS]


def unseen_count(items, seen_at):
    """Wie viele Probleme sind neuer als der Zeitpunkt, an dem das Problemfenster zuletzt geöffnet wurde? (Pausen von Konten und Kanälen
    zählen nicht mit: sie sind kein Fehler, den man bestätigen müsste, sondern ein Zustand.)"""
    cut = _parse(seen_at) if seen_at else None
    n = 0
    for item in items:
        if item['kind'] in ('account',) or item['key'].startswith('chanpause'):
            continue
        if cut is None or item['time'] > cut:
            n += 1
    return n


def when_text(moment, now=None):
    """„14:05“, „gestern 14:05“ oder „11.10. 14:05“ (Ortszeit)."""
    return al.local_text(repo.iso(moment), now)
