"""Logik der Startseite „Übersicht“ ohne Tk: Zustand einsammeln, Karten, Hinweise, Erste-Schritte-Liste.

`collect(...)` liest nur (schreibgeschützt, keine Datei wird angelegt) und läuft im Hintergrund-Thread.
`cards`, `attention` und `checklist` sind reine Funktionen über das gesammelte Wörterbuch (`snap`) und lassen sich ohne Fenster testen.

Ein Hinweis („Das braucht deine Aufmerksamkeit“) ist ein Wörterbuch:
  key, level ('err' | 'warn' | 'info'), title, text, action = {'id': …, 'label': …, …}
Die Aktionen führt das Fenster aus (`ui/tab_home.py`, `App.run_action`).
"""
import datetime
import pathlib
import sqlite3

from . import aiconfig
from . import autologic as al
from . import guicore, proxylogic, repo
from . import helptexts
from .platforms import PLATFORM_NAMES

BACKUP_WARN_DAYS = 14
LEVEL_ORDER = {'err': 0, 'warn': 1, 'info': 2}
ACCOUNT_PLATFORMS = ('tiktok', 'instagram', 'reddit', 'x')
NEEDS_LOGIN = ('tiktok', 'instagram', 'x')       # ohne Anmeldung kaum nutzbar (Reddit: freiwillig)


def _plural(n, one, many):
    return f'{n} {one if n == 1 else many}'


def _fmt_n(n):
    return f'{int(n):,}'.replace(',', '.')


def when_text(moment, now=None):
    """Uhrzeit in Ortszeit: „14:30“, „morgen 00:30“, „13.10. 14:30“."""
    from .rotation import fmt_when
    return fmt_when(moment, now)


# ---------------------------------------------------------------- Einsammeln (Hintergrund-Thread)

def newest_backup(root):
    """Zeitpunkt (UTC) der neuesten Sicherung in data/backups, sonst None."""
    folder = pathlib.Path(root) / 'data' / 'backups'
    best = None
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in ('.db', '.sqlite', '.bak'):
                m = p.stat().st_mtime
                best = m if best is None or m > best else best
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(best, datetime.timezone.utc) if best else None


def collect(root, db_path=None, now=None, *, system_ok=None, ui_state=None):
    """Alles, was die Startseite zeigt, in einem Rutsch. Fehlt etwas oder ist es unlesbar, bleiben leere Werte (nie eine Ausnahme).

    `system_ok`: Ergebnis von ui.common.system_check() (langsam: das macht der Aufrufer im Hintergrund und reicht es herein).
    `ui_state`: Wörterbuch aus uistate (für „zuletzt gesichert“)."""
    root = pathlib.Path(root)
    now = now or repo.now()
    db_path = pathlib.Path(db_path) if db_path else al.resolve_db(root)
    snap = {'now': now, 'root': str(root), 'error': None, 'db_exists': db_path.is_file(),
            'mode': proxylogic.current_mode(root), 'service': al.service_status(root),
            'accounts': [], 'targets': [], 'channels': [], 'runs': [], 'due': 0,
            'counts': {'videos': 0, 'comments': 0, 'people': 0, 'new_comments': 0, 'new_authors': 0, 'new_videos': 0},
            'system_ok': system_ok, 'ai_key': False, 'ai_auto': False, 'busy': None,
            'last_backup': None, 'manual_runs': 0, 'has_platform_data': {}}
    try:
        cfg = aiconfig.load(root)
        snap['ai_key'] = bool(aiconfig.has_key(cfg))
        snap['ai_auto'] = bool(cfg.get('auto_after_runs'))
    except Exception:
        pass
    try:
        from . import runlock
        lock_root = runlock.root_for_db(guicore.db_path(root))   # dieselbe Sperrdatei wie Befehle und Dienst
        if runlock.lock_path(lock_root).exists():
            snap['busy'] = runlock.holder(lock_root)
    except Exception:
        pass
    last = newest_backup(root)
    saved = (ui_state or {}).get('last_backup_at')
    try:
        saved_dt = repo.parse(saved) if saved else None
    except ValueError:
        saved_dt = None
    snap['last_backup'] = max([d for d in (last, saved_dt) if d], default=None)
    if not db_path.is_file():
        return snap
    try:
        with al._read(db_path) as db:
            if db is None:
                return snap
            _read_db(db, snap, root, now)
    except sqlite3.OperationalError as error:
        text = str(error).lower()
        snap['error'] = 'Die Datenbank ist gerade beschäftigt.' if ('locked' in text or 'busy' in text) else 'Die Datenbank lässt sich nicht lesen.'
    except sqlite3.DatabaseError:
        snap['error'] = 'Die Datenbank ist beschädigt oder keine Datenbank.'
    return snap


def _safe(snap, key, fn, default):
    try:
        return fn()
    except sqlite3.OperationalError as error:
        if 'no such' in str(error).lower():
            return default
        raise


def _read_db(db, snap, root, now):
    since = repo.iso(now - datetime.timedelta(hours=24))
    c = snap['counts']
    c['videos'] = _safe(snap, 'videos', lambda: db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 0)
    c['comments'] = _safe(snap, 'comments', lambda: db.execute('SELECT COUNT(*) FROM comments').fetchone()[0], 0)
    cut = since.replace(' ', 'T')[:19]
    sql_t = "substr(replace({}, ' ', 'T'), 1, 19)"
    c['new_comments'] = _safe(snap, 'nc', lambda: db.execute(
        f'SELECT COUNT(*) FROM comments WHERE {sql_t.format("first_seen_at")} >= ?', (cut,)).fetchone()[0], 0)
    c['new_videos'] = _safe(snap, 'nv', lambda: db.execute(
        f'SELECT COUNT(*) FROM videos WHERE {sql_t.format("first_seen_at")} >= ?', (cut,)).fetchone()[0], 0)
    try:
        from . import people_repo
        c['new_authors'] = people_repo.count_new(db, 'commenter', since)
        c['people'] = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT platform, ss_ak(author_id, author) AS k FROM comments "
                                 "WHERE ss_ak(author_id, author) <> '')").fetchone()[0]
    except Exception:
        pass
    snap['targets'] = [t for t in _safe(snap, 'targets', lambda: repo.list_targets(db), []) if t.get('kind') != 'channel']
    snap['channels'] = _safe(snap, 'channels', lambda: repo.list_channels(db), [])
    snap['has_platform_data'] = {r[0]: True for r in _safe(snap, 'pd', lambda: db.execute('SELECT DISTINCT platform FROM videos').fetchall(), [])}
    snap['runs'] = _safe(snap, 'runs', lambda: repo.list_runs(db, 30), [])
    snap['due'] = len(_safe(snap, 'due', lambda: repo.due_targets(db, now), []))
    snap['manual_runs'] = _safe(snap, 'mr', lambda: db.execute("SELECT COUNT(*) FROM runs WHERE source='manual'").fetchone()[0], 0)
    accounts = []
    from .rotation import account_state
    for acc in _safe(snap, 'accounts', lambda: repo.list_accounts(db), []):
        state = account_state(acc, now, root)
        login = guicore.account_status(acc['platform'], root, acc['identity'])[0]
        accounts.append({'id': acc['id'], 'platform': acc['platform'], 'label': acc['label'], 'default': bool(acc['is_default']),
                         'in_rotation': bool(acc.get('rotation_enabled', 1)), 'login': login, 'state': state['state'],
                         'until': state['until'], 'reason': acc.get('cooldown_reason') if state['state'] == 'abkuehlung' else None,
                         'text': state['text']})
    snap['accounts'] = accounts


# ---------------------------------------------------------------- Karten

def service_card(snap):
    status = snap.get('service') or {}
    due = snap.get('due', 0)
    if status.get('unavailable'):
        return {'key': 'service', 'title': 'Hintergrunddienst', 'value': 'nicht vorhanden', 'detail': '', 'kind': 'muted', 'button': None}
    if not status.get('running'):
        detail = f'{_plural(due, "Ziel ist", "Ziele sind")} fällig – es passiert nichts, solange er aus ist.' if due else 'Automatische Abrufe laufen nur, solange er an ist.'
        return {'key': 'service', 'title': 'Hintergrunddienst', 'value': 'Aus', 'detail': detail, 'kind': 'warn' if due else 'muted',
                'button': ('start_service', 'Starten')}
    if status.get('stale'):
        return {'key': 'service', 'title': 'Hintergrunddienst', 'value': 'Läuft, meldet sich nicht', 'detail': 'Eventuell hängt ein Abruf.',
                'kind': 'warn', 'button': ('stop_service', 'Stoppen')}
    cur = status.get('current')
    detail = 'Arbeitet gerade.' if cur else al.next_run_summary(snap.get('targets', []) + snap.get('channels', []), snap.get('now'))
    return {'key': 'service', 'title': 'Hintergrunddienst', 'value': 'An', 'detail': detail, 'kind': 'ok', 'button': ('stop_service', 'Stoppen')}


def proxy_card(snap):
    mode = snap.get('mode', 'aus')
    kind = 'warn' if proxylogic.is_anonymous(mode) else 'ok'
    title = proxylogic.MODE_TITLES.get(mode, mode)
    short = {'aus': 'Aus', 'liste': 'Eigene Liste', 'frei': 'Kostenlose Proxys', 'tor': 'Tor'}.get(mode, mode)
    return {'key': 'proxy', 'title': 'Verbindung (Proxy-Modus)', 'value': short, 'detail': helptexts.MODE_EXPLAIN.get(mode, title),
            'kind': kind, 'button': ('goto_proxy', 'Ändern')}


def accounts_card(snap):
    """Je Plattform eine Zeile: „TikTok: 1 bereit · 1 Pause · 0 nicht angemeldet“."""
    rows = []
    by = {}
    for a in snap.get('accounts', []):
        by.setdefault(a['platform'], []).append(a)
    kind = 'ok'
    for plat in ACCOUNT_PLATFORMS:
        accs = by.get(plat, [])
        if not accs:
            continue
        ready = [a for a in accs if a['state'] == 'bereit' and (a['login'] == 'saved' or plat == 'reddit')]
        cooling = [a for a in accs if a['state'] == 'abkuehlung']
        nologin = [a for a in accs if a['login'] != 'saved' and plat != 'reddit']
        parts = [f'{len(ready)} bereit']
        if cooling:
            parts.append(f'{len(cooling)} in Pause')
            kind = 'warn' if kind == 'ok' else kind
        if nologin:
            parts.append(f'{len(nologin)} nicht angemeldet')
            if plat in NEEDS_LOGIN and not ready:
                kind = 'warn'
        rows.append(f'{PLATFORM_NAMES[plat]}: ' + ' · '.join(parts))
    if not rows:
        return {'key': 'accounts', 'title': 'Konten', 'value': 'keine', 'detail': 'YouTube geht ohne Konto.', 'kind': 'muted',
                'button': ('goto_accounts', 'Zu den Konten'), 'rows': []}
    if proxylogic.is_anonymous(snap.get('mode')):
        kind = 'warn'
        rows.append('Gesperrt durch den Proxy-Modus.')
    return {'key': 'accounts', 'title': 'Konten', 'value': '', 'detail': '', 'kind': kind, 'button': ('goto_accounts', 'Zu den Konten'), 'rows': rows}


def channels_card(snap):
    chans = snap.get('channels', [])
    from . import channels as ch
    now = snap.get('now')
    items = []
    for t in chans:
        state = t.get('backfill_state') or 'queued'
        if state in ('done', 'watching') and t.get('enabled'):
            continue
        pr = t.get('progress') or {}
        total, done = pr.get('total', 0), pr.get('finished', 0)
        frac = (done / total) if total else 0.0
        count, text, when = ch.progress_parts(t, now)
        nxt = f'nächster Schritt {when}' if when else ''
        kind = 'warn' if state == 'paused' or not t.get('enabled') else 'ok'
        items.append({'id': t['id'], 'name': al.target_title(t), 'frac': frac, 'count': count, 'state': text, 'next': nxt, 'kind': kind})
    watching = sum(1 for t in chans if (t.get('backfill_state') in ('done', 'watching')) and t.get('enabled'))
    return {'key': 'channels', 'title': 'Kanäle in Arbeit', 'items': items, 'watching': watching, 'total': len(chans),
            'button': ('goto_channels', 'Zu den Kanälen')}


def data_card(snap):
    c = snap.get('counts', {})
    return {'key': 'data', 'title': 'Daten', 'videos': c.get('videos', 0), 'comments': c.get('comments', 0), 'people': c.get('people', 0),
            'new_comments': c.get('new_comments', 0), 'new_authors': c.get('new_authors', 0), 'new_videos': c.get('new_videos', 0),
            'button': ('goto_data', 'Daten ansehen')}


def last_run_card(snap):
    now = snap.get('now')
    runs = [r for r in snap.get('runs', []) if r.get('status') != 'running']
    running = [r for r in snap.get('runs', []) if r.get('status') == 'running']
    if not runs and not running:
        return {'key': 'last_run', 'title': 'Letzter Lauf', 'value': 'noch keiner', 'detail': 'Sobald ein Abruf gelaufen ist, steht hier das Ergebnis.',
                'kind': 'muted'}
    if running and not runs:
        return {'key': 'last_run', 'title': 'Letzter Lauf', 'value': 'läuft gerade', 'detail': '', 'kind': 'warn'}
    r = runs[0]
    name = al.target_title({'kind': r['target_kind'], 'platform': r.get('target_platform') or '', 'value': r.get('target_value') or ''}) \
        if r.get('target_kind') else (al.deleted_title(r.get('label')) if r.get('label') else 'Abruf von Hand')
    result = {'ok': 'geklappt', 'partial': 'teilweise geklappt', 'failed': 'fehlgeschlagen', 'cancelled': 'abgebrochen'}.get(r['status'], r['status'])
    kind = {'ok': 'ok', 'partial': 'warn', 'failed': 'err', 'cancelled': 'muted'}.get(r['status'], 'muted')
    when = al.local_text(r.get('finished_at') or r.get('started_at'), now)
    detail = f'{name} · {when}'
    if r['status'] in ('ok', 'partial'):
        detail += f' · {_plural(r.get("videos_new") or 0, "neues Video", "neue Videos")}, {_plural(r.get("comments_new") or 0, "neuer Kommentar", "neue Kommentare")}'
    return {'key': 'last_run', 'title': 'Letzter Lauf', 'value': result, 'detail': detail, 'kind': kind}


def cards(snap):
    return {'service': service_card(snap), 'proxy': proxy_card(snap), 'accounts': accounts_card(snap), 'channels': channels_card(snap),
            'data': data_card(snap), 'last_run': last_run_card(snap)}


# ---------------------------------------------------------------- Hinweise

def _uses_platform(snap, platform):
    for t in snap.get('targets', []) + snap.get('channels', []):
        if t.get('platform') == platform:
            return True
    return False


def attention(snap, dismissed=None):
    """Liste der Hinweise, wichtigste zuerst. `dismissed`: Schlüssel, die der Nutzer weggeklickt hat."""
    items = []
    now = snap.get('now') or repo.now()
    mode = snap.get('mode', 'aus')
    anonymous = proxylogic.is_anonymous(mode)

    def add(key, level, title, text, action=None, extra=None):
        item = {'key': key, 'level': level, 'title': title, 'text': text, 'action': action}
        if extra:
            item.update(extra)
        items.append(item)

    if snap.get('error'):
        add('db_error', 'err', 'Die Datenbank lässt sich nicht lesen', snap['error'] + ' Starte das Programm neu, wenn es so bleibt.',
            {'id': 'open_problems', 'label': 'Probleme ansehen'})
    if snap.get('system_ok') is False:
        add('system', 'err', 'Die Einrichtung ist unvollständig',
            'Etwas fehlt (zum Beispiel Chrome oder ein Programmteil). Führe „Einrichten.cmd“ im Programmordner aus.',
            {'id': 'goto_system', 'label': 'Details ansehen'})

    # Proxy-Modus
    if anonymous:
        blocked = [t for t in snap.get('targets', []) + snap.get('channels', []) if proxylogic.target_problem(mode, t)]
        needs = [a for a in snap.get('accounts', []) if a['platform'] in NEEDS_LOGIN]
        level = 'warn' if (blocked or needs) else 'info'
        text = 'Anmelden, Instagram, X und TikTok-Kommentare gehen in diesem Modus nicht.'
        if blocked:
            text += f' {_plural(len(blocked), "Ziel ist", "Ziele sind")} dadurch blockiert.'
        add('proxy_mode', level, f'Proxy-Modus „{proxylogic.MODE_TITLES[mode]}“ sperrt Konten', text,
            {'id': 'set_mode', 'label': 'Auf „Aus“ stellen', 'value': 'aus', 'from': mode})

    # Anmeldung
    if not anonymous:
        for plat in NEEDS_LOGIN:
            accs = [a for a in snap.get('accounts', []) if a['platform'] == plat]
            if not accs or any(a['login'] == 'saved' for a in accs):
                continue
            if not (_uses_platform(snap, plat) or snap.get('has_platform_data', {}).get(plat)):
                continue
            acc = next((a for a in accs if a['default']), accs[0])
            add(f'login:{plat}', 'warn', f'{PLATFORM_NAMES[plat]}-Konto nicht angemeldet',
                f'{PLATFORM_NAMES[plat]} braucht eine Anmeldung, sonst bleiben Abrufe und Ziele hängen.',
                {'id': 'login', 'label': 'Jetzt anmelden', 'account_id': acc['id']})

    # Kein Konto frei
    for plat in ACCOUNT_PLATFORMS:
        accs = [a for a in snap.get('accounts', []) if a['platform'] == plat and a['in_rotation']]
        if not accs or plat == 'reddit' or anonymous:
            continue
        free = [a for a in accs if a['state'] == 'bereit' and a['login'] == 'saved']
        cooling = [a for a in accs if a['state'] in ('abkuehlung', 'tageslimit')]
        if not free and cooling and _uses_platform(snap, plat):
            untils = [a['until'] for a in cooling if a.get('until')]
            when = f' Das früheste ist ab {when_text(min(untils), now)} wieder dran.' if untils else ''
            add(f'noacc:{plat}', 'warn', f'Kein {PLATFORM_NAMES[plat]}-Konto frei',
                f'Alle {PLATFORM_NAMES[plat]}-Konten machen gerade Pause.{when}',
                {'id': 'goto_account', 'label': 'Konten ansehen', 'account_id': cooling[0]['id']})
        else:
            for a in cooling:
                if a['state'] == 'abkuehlung':
                    add(f'cool:{a["id"]}:{a["until"]}', 'info', f'{PLATFORM_NAMES[plat]}-Konto „{a["label"]}“ macht Pause',
                        f'{a["text"][:1].upper()}{a["text"][1:]}. In der Pause wird es nicht benutzt.',
                        {'id': 'goto_account', 'label': 'Details', 'account_id': a['id']})

    # Kanäle
    for t in snap.get('channels', []):
        name = al.target_title(t)
        pr = t.get('progress') or {}
        if t.get('backfill_state') == 'paused' and t.get('enabled'):
            until = al.parse_utc(t.get('pause_until'))
            when = f' bis {when_text(until, now)}' if until else ''
            reason = f' wegen {t["pause_reason"]}' if t.get('pause_reason') else ''
            add(f'chan_pause:{t["id"]}:{t.get("pause_until")}', 'warn', f'{name} pausiert{when}{reason}',
                'Der Kanal macht von allein weiter. Nichts tun, außer du willst die Ursache ansehen.',
                {'id': 'goto_channel', 'label': 'Details', 'target_id': t['id']})
        elif not t.get('enabled') and t.get('backfill_state') not in ('done',):
            add(f'chan_off:{t["id"]}', 'info', f'{name} ist angehalten', 'Du hast diesen Kanal angehalten. Er holt nichts, bis du ihn fortsetzt.',
                {'id': 'resume_channel', 'label': 'Fortsetzen', 'target_id': t['id']})
        if pr.get('failed'):
            add(f'chan_fail:{t["id"]}:{pr["failed"]}', 'info', f'{name}: {_plural(pr["failed"], "Video", "Videos")} fehlgeschlagen',
                'Du kannst sie noch einmal versuchen lassen.', {'id': 'requeue_channel', 'label': 'Noch einmal versuchen', 'target_id': t['id']})

    # Hintergrunddienst
    status = snap.get('service') or {}
    if not status.get('unavailable'):
        if not status.get('running') and snap.get('due', 0):
            n = snap['due']
            add('service_off', 'warn', 'Der Hintergrunddienst läuft nicht',
                f'{_plural(n, "Ziel ist", "Ziele sind")} fällig, aber ohne Dienst wird nichts abgerufen.',
                {'id': 'start_service', 'label': 'Dienst starten'})
        elif status.get('running') and status.get('stale'):
            add('service_stale', 'warn', 'Der Hintergrunddienst meldet sich nicht mehr', 'Eventuell hängt ein Abruf. Stoppen und neu starten hilft.',
                {'id': 'stop_service', 'label': 'Dienst stoppen'})

    # Letzter Lauf fehlgeschlagen (nur wenn jüngst und noch kein späterer Erfolg desselben Ziels)
    seen_targets = set()
    for r in snap.get('runs', []):
        if r.get('status') == 'running':
            continue
        tid = r.get('target_id')
        if tid in seen_targets:
            continue
        seen_targets.add(tid)
        if r.get('status') == 'failed':
            finished = al.parse_utc(r.get('finished_at') or r.get('started_at'))
            if finished and (now - finished) <= datetime.timedelta(hours=24):
                name = al.target_title({'kind': r['target_kind'], 'platform': r.get('target_platform') or '', 'value': r.get('target_value') or ''}) \
                    if r.get('target_kind') else 'Ein Abruf'
                from . import problemlogic
                head, _todo, _goto = problemlogic.explain(r.get('error') or '')
                add(f'run_fail:{r["id"]}', 'warn', f'{name} ist zuletzt fehlgeschlagen', head,
                    {'id': 'open_problems', 'label': 'Probleme ansehen'})
                break

    # KI-Schlüssel
    if not snap.get('ai_key') and (snap.get('counts', {}).get('comments', 0) or snap.get('ai_auto')):
        add('ai_key', 'warn' if snap.get('ai_auto') else 'info', 'Der KI-Schlüssel fehlt',
            'Ohne ihn lässt sich die Stimmung nicht bestimmen' + (', und die automatische Stimmung nach jedem Lauf ist an.' if snap.get('ai_auto') else '.'),
            {'id': 'goto_ai', 'label': 'Zu den Einstellungen'})

    # Sicherung
    if snap.get('db_exists') and snap.get('counts', {}).get('videos', 0):
        last = snap.get('last_backup')
        if last is None:
            add('backup', 'info', 'Deine Daten sind noch nie gesichert worden', 'Eine Sicherungskopie schützt vor Datenverlust (zum Beispiel bei einem Festplattenfehler).',
                {'id': 'backup', 'label': 'Sicherungskopie anlegen'})
        elif (now - last) > datetime.timedelta(days=BACKUP_WARN_DAYS):
            days = (now - last).days
            add('backup', 'info', f'Die Daten wurden seit {days} Tagen nicht gesichert', 'Lege zur Sicherheit eine neue Sicherungskopie an.',
                {'id': 'backup', 'label': 'Sicherungskopie anlegen'})

    dismissed = set(dismissed or ())
    items = [i for i in items if i['key'] not in dismissed]
    items.sort(key=lambda i: LEVEL_ORDER[i['level']])
    return items


# ---------------------------------------------------------------- Erste Schritte

def checklist(snap, state=None):
    """Die fünf Schritte mit Haken, aus dem echten Zustand. `state`: das Wörterbuch `home` aus uistate."""
    state = state or {}
    counts = snap.get('counts', {})
    mode = snap.get('mode', 'aus')
    login_saved = any(a['login'] == 'saved' for a in snap.get('accounts', []) if a['platform'] in NEEDS_LOGIN or a['platform'] == 'reddit')
    steps = [
        {'key': 'system', 'title': 'Systemprüfung ist in Ordnung', 'done': snap.get('system_ok') is True,
         'text': 'Chrome und die nötigen Programmteile sind vorhanden.' if snap.get('system_ok') is True else
                 ('Es fehlt etwas. Führe „Einrichten.cmd“ aus.' if snap.get('system_ok') is False else 'Wird geprüft …'),
         'action': {'id': 'goto_system', 'label': 'Details'} if snap.get('system_ok') is False else None},
        {'key': 'account', 'title': 'Konto anlegen und anmelden', 'done': login_saved or bool(state.get('skip_account')),
         'text': ('Ein Konto ist angemeldet.' if login_saved else 'Du brauchst nur YouTube oder Reddit? Dann überspringe diesen Schritt.'
                  if state.get('skip_account') else 'TikTok-Kommentare, Instagram und X brauchen eine Anmeldung. YouTube geht ohne.'),
         'action': {'id': 'goto_accounts', 'label': 'Zu den Konten'}, 'skip': None if login_saved or state.get('skip_account') else
                  {'id': 'skip_account', 'label': 'Brauche ich nicht'}},
        {'key': 'proxy', 'title': 'Verbindung wählen', 'done': bool(state.get('proxy_ok')),
         'text': f'Aktuell: {proxy_card(snap)["value"]}. ' + helptexts.MODE_EXPLAIN.get(mode, ''),
         'action': {'id': 'goto_proxy', 'label': 'Ändern'},
         'skip': None if state.get('proxy_ok') else {'id': 'proxy_ok', 'label': 'So lassen'}},
        {'key': 'fetch', 'title': 'Ersten Link abrufen', 'done': counts.get('videos', 0) > 0 or snap.get('manual_runs', 0) > 0,
         'text': 'Füge im Tab „Abrufen“ einen Video-Link ein.' if counts.get('videos', 0) == 0 else f'{_plural(counts["videos"], "Video", "Videos")} gespeichert.',
         'action': {'id': 'goto_fetch', 'label': 'Link abrufen'}},
        {'key': 'view', 'title': 'Ergebnis ansehen', 'done': bool(state.get('viewed_data')) and counts.get('videos', 0) > 0,
         'text': 'Videos und Kommentare findest du im Tab „Gespeicherte Daten“.',
         'action': {'id': 'goto_data', 'label': 'Daten ansehen'}},
    ]
    return steps


def checklist_visible(steps, state=None):
    """Zeigen, solange nicht alles erledigt ist und der Nutzer sie nicht weggeklickt hat."""
    if (state or {}).get('checklist_hidden'):
        return False
    return not all(s['done'] for s in steps)
