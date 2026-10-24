"""Befehl `channel`: ganze Kanäle von der Befehlszeile aus verwalten (dieselbe Logik und dieselben Prüfungen wie der Dialog).

    channel add <link> [--scope all|latest|since] [--latest N] [--since JJJJ-MM-TT] [--max-comments N] [--group NAME]
                       [--account-mode default|fixed|rotate] [--account-id ID] [--pace vorsichtig|normal|schnell]
                       [--quiet 23-7] [--watch | --no-watch] [--interval MIN]
    channel list                 alle Kanäle mit Fortschritt
    channel status ID            ein Kanal im Detail (auch fehlgeschlagene Videos)
    channel pause ID | resume ID | retry-failed ID | remove ID
    channel estimate <link> --count N [--pace …] [--accounts N] [--comments N] [--quiet 23-7]   (Zeitschätzung ohne Netz)

Ausgaben sind deutsch. Exit-Codes: 0 = erledigt, 1 = Eingabe- oder Datenbankfehler (Meldung auf der Fehlerausgabe),
2 = ein Kanal mit dieser Nummer gibt es nicht. Nichts hier greift ins Netz.
"""
import argparse
import sys

NAME = 'channel'
HELP = 'ganze Kanäle verwalten: add, list, status, pause, resume, retry-failed, remove, estimate'

PACE = {'vorsichtig': 'careful', 'normal': 'normal', 'schnell': 'fast'}
ACTIONS = ('add', 'list', 'status', 'pause', 'resume', 'retry-failed', 'remove', 'estimate')
MAX_FAILED_SHOWN = 10


def add_arguments(p):
    sub = p.add_subparsers(dest='aktion', metavar='AKTION')
    sub.required = True

    add = sub.add_parser('add', help='Kanal anlegen')
    add.add_argument('link', help='Link zum Kanal oder Profil')
    add.add_argument('--scope', choices=['all', 'latest', 'since'], default=None,
                     help='Umfang: all = alle Videos, latest = nur die neuesten (mit --latest), since = ab Datum (mit --since)')
    add.add_argument('--latest', type=int, default=None, metavar='N', help='Zahl der neuesten Videos (1 bis 100000)')
    add.add_argument('--since', default=None, metavar='DATUM', help='ab diesem Datum, so: 2026-01-31')
    add.add_argument('--max-comments', type=int, default=0, metavar='N', help='Kommentare je Video (0 bis 5000, Standard 0)')
    add.add_argument('--group', default=None, metavar='NAME', help='Videos in diese Gruppe sortieren (wird angelegt, falls neu)')
    add.add_argument('--account-mode', choices=['default', 'fixed', 'rotate'], default=None,
                     help='default = Standardkonto, fixed = bestimmtes Konto (mit --account-id), rotate = Rotation')
    add.add_argument('--account-id', type=int, default=None, metavar='ID', help='Nummer des Kontos (siehe Konten)')
    add.add_argument('--pace', choices=sorted(PACE), default='normal', help='Tempo-Profil (Standard normal)')
    add.add_argument('--quiet', default=None, metavar='VON-BIS',
                     help='Ruhezeit in Uhr, zum Beispiel 23-7 (nachts nichts abrufen); „aus“ = keine')
    add.add_argument('--watch', action=argparse.BooleanOptionalAction, default=True,
                     help='Neue Videos danach beobachten (Standard); --no-watch = nach dem letzten Video fertig')
    add.add_argument('--interval', type=int, default=360, metavar='MIN', help='Abstand beim Beobachten in Minuten (5 bis 43200, Standard 360)')

    sub.add_parser('list', help='alle Kanäle mit Fortschritt')
    for name, text in (('status', 'ein Kanal im Detail'), ('pause', 'Kanal anhalten'), ('resume', 'Kanal fortsetzen'),
                       ('retry-failed', 'fehlgeschlagene Videos noch einmal versuchen'),
                       ('remove', 'Kanal entfernen (gespeicherte Videos bleiben)')):
        one = sub.add_parser(name, help=text)
        one.add_argument('id', type=int, help='Nummer des Kanals (siehe „channel list“)')

    est = sub.add_parser('estimate', help='Zeitschätzung ohne Netz')
    est.add_argument('link', help='Link zum Kanal oder Profil (nur für die Plattform)')
    est.add_argument('--count', type=int, required=True, metavar='N', help='Zahl der Videos')
    est.add_argument('--pace', choices=sorted(PACE), default='normal')
    est.add_argument('--accounts', type=int, default=1, metavar='N', help='Zahl der Konten in der Rotation (Standard 1)')
    est.add_argument('--comments', type=int, default=0, metavar='N', help='Kommentare je Video')
    est.add_argument('--quiet', default=None, metavar='VON-BIS', help='Ruhezeit, zum Beispiel 23-7')


# ---------- Prüfungen (gleiche Regeln wie der Dialog) ----------

def _quiet(text):
    from .. import repo
    text = (text or '').strip()
    if text.lower() in ('', 'aus', 'none', 'nein', 'keine'):
        return None
    repo.check_quiet_hours(text)
    return text


def _scope(args):
    """(scope, scope_value) aus --scope/--latest/--since; ValueError mit deutscher Meldung bei Widersprüchen."""
    scope = args.scope
    if scope is None:
        if args.latest is not None and args.since is not None:
            raise ValueError('Bitte entweder --latest oder --since angeben, nicht beides.')
        scope = 'latest' if args.latest is not None else 'since' if args.since is not None else 'all'
    if scope == 'latest':
        if args.since is not None:
            raise ValueError('--since passt nicht zu --scope latest.')
        if args.latest is None:
            raise ValueError('Bei --scope latest bitte die Zahl mit --latest N angeben.')
        if not 1 <= args.latest <= 100000:
            raise ValueError('Die Zahl der neuesten Videos muss zwischen 1 und 100000 liegen.')
        return 'latest', args.latest
    if scope == 'since':
        import datetime
        if args.latest is not None:
            raise ValueError('--latest passt nicht zu --scope since.')
        if args.since is None:
            raise ValueError('Bei --scope since bitte das Datum mit --since JJJJ-MM-TT angeben.')
        try:
            datetime.date.fromisoformat(args.since.strip())
        except ValueError:
            raise ValueError('Bitte das Datum so schreiben: 2026-01-31 (Jahr-Monat-Tag).') from None
        return 'since', args.since.strip()
    if args.latest is not None or args.since is not None:
        raise ValueError('--latest und --since passen nicht zu --scope all.')
    return 'all', None


def _account(db, ref, args):
    """(account_mode, account_id, Hinweise). Das Konto muss zur Plattform des Kanals gehören."""
    from .. import repo
    notes = []
    mode, account_id = args.account_mode, args.account_id
    if mode is None:
        mode = 'fixed' if account_id is not None else 'default'
    if mode == 'fixed' and account_id is None:
        raise ValueError('Für --account-mode fixed bitte das Konto mit --account-id angeben.')
    if mode != 'fixed' and account_id is not None:
        raise ValueError('--account-id passt nur zu --account-mode fixed.')
    if ref.platform == 'youtube':
        if mode != 'default':
            notes.append('YouTube braucht kein Konto: Die Kontoangabe wird ignoriert.')
        return 'default', None, notes
    if mode == 'fixed':
        account = repo.get_account(db, account_id)
        if account is None:
            raise ValueError(f'Ein Konto mit der Nummer {account_id} gibt es nicht.')
        if account['platform'] != ref.platform:
            raise ValueError(f'Das Konto „{account["label"]}“ gehört zu {account["platform"]}, der Kanal zu {ref.platform}.')
    return mode, account_id, notes


def _group(db, name):
    """Gruppe nach Name (Groß-/Kleinschreibung egal); eine neue wird angelegt. Gibt (id, Hinweis) zurück."""
    from .. import repo
    name = (name or '').strip()
    if not name:
        return None, None
    for group in repo.list_groups(db):
        if group['name'].casefold() == name.casefold():
            return group['id'], None
    return repo.create_group(db, name)['id'], f'Die Gruppe „{name}“ wurde neu angelegt.'


# ---------- Ausgabe ----------

def _find(cfg, target_id):
    from .. import chanlogic
    return next((t for t in chanlogic.load(cfg.db_path, strict=True) if t['id'] == target_id), None)   # belegt -> Meldung, Exit 1


def _missing(target_id):
    print(f'Einen Kanal mit der Nummer {target_id} gibt es nicht. Mit „channel list“ siehst Du die Nummern.', file=sys.stderr)
    return 2


def _title(target):
    from .. import channels
    return f'{channels.PLATFORM_NAMES.get(target["platform"], target["platform"])} {target.get("display_name") or target["value"]}'


def _scope_text(target):
    scope = target.get('scope')
    if scope == 'latest':
        return f'die neuesten {target.get("scope_value")}'
    if scope == 'since':
        return f'ab {target.get("scope_value")}'
    return 'alle Videos'


def _list(cfg):
    from .. import channels, chanlogic
    rows = chanlogic.load(cfg.db_path, strict=True)   # belegt -> Meldung, Exit 1 (cli.main)
    if not rows:
        print('Noch keine Kanäle. Mit „channel add <link>“ legst Du einen an.')
        return 0
    for target in rows:
        print(f'{target["id"]:>3}  {_title(target)}  [{chanlogic.PROFILE_LABELS.get(target.get("pace_profile"), "Normal")}]')
        print(f'     {channels.progress_line(target)}')
    print(f'{len(rows)} {"Kanal" if len(rows) == 1 else "Kanäle"}.')
    return 0


def _status(cfg, target):
    from .. import channels, chanlogic
    from ..scheduler import clean_error
    print(f'Kanal {target["id"]}: {_title(target)}')
    print(f'Fortschritt: {channels.progress_line(target)}')
    pr = target.get('progress') or {}
    print(f'Videos: {pr.get("done", 0)} fertig, {pr.get("skipped", 0)} übersprungen, {pr.get("pending", 0)} offen, '
          f'{pr.get("running", 0)} laufen, {pr.get("failed", 0)} fehlgeschlagen')
    print(f'Umfang: {_scope_text(target)}')
    print(f'Tempo: {chanlogic.profile_text(target.get("pace_profile") or "normal")}')
    print(f'Ruhezeit: {channels.quiet_text(target.get("quiet_hours"))}')
    mode = target.get('account_mode') or 'default'
    print('Konto: ' + {'default': 'Standardkonto', 'rotate': 'Rotation unter den Konten',
                       'fixed': f'bestimmtes Konto ({target.get("account_label") or target.get("account_id")})'}.get(mode, mode))
    print(f'Gruppe: {target.get("group_name") or "keine"}')
    print(f'Kommentare je Video: {target.get("max_comments") or 0}')
    print('Beobachten: ' + (f'ja, alle {target.get("interval_min")} Minuten' if target.get('observe') else 'nein'))
    if target.get('pause_reason'):
        print(f'Pause wegen: {target["pause_reason"]}')
    if target.get('last_error'):
        print(f'Letzter Fehler: {clean_error(target["last_error"], limit=160)}')
    if pr.get('failed'):
        failed = chanlogic.items(cfg.db_path, target['id'], 'failed', MAX_FAILED_SHOWN + 1)
        print('Fehlgeschlagen:')
        for item in failed[:MAX_FAILED_SHOWN]:
            reason = clean_error(item.get('last_error') or 'unbekannt', limit=120)
            print(f'  {item["video_url"]}  –  {reason}')
        if len(failed) > MAX_FAILED_SHOWN:
            print(f'  … und weitere. Mit „channel retry-failed {target["id"]}“ kommen alle noch einmal dran.')
    return 0


def _estimate(args):
    from .. import channels
    ref = channels.parse_channel_link(args.link)
    if args.count < 0:
        raise ValueError('--count darf nicht negativ sein.')
    if args.accounts < 1:
        raise ValueError('--accounts muss mindestens 1 sein.')
    if not 0 <= args.comments <= 5000:
        raise ValueError('Kommentare pro Video: 0 bis 5000.')
    quiet = _quiet(args.quiet)
    profile = PACE[args.pace]
    e = channels.estimate(args.count, ref.platform, profile, accounts=args.accounts, comments=args.comments, quiet_hours=quiet)
    print(f'{channels.PLATFORM_NAMES[ref.platform]} {ref.display}: {e["videos"]} Videos bei Tempo „{channels.PROFILES[profile]["name"]}“: {e["text"]}.')
    print('Rechnung: ' + e['formula'] + '.')
    print('Das sind die Grenzen, die sich das Programm selbst setzt – keine Messung; die Plattform kann früher oder später bremsen.')
    for note in e['notes']:
        print('Hinweis: ' + note)
    return 0


def run(args, cfg):
    from .. import autologic as al
    from .. import channels, chanlogic
    action = args.aktion
    if action == 'list':
        return _list(cfg)
    if action == 'estimate':
        return _estimate(args)
    if action == 'add':
        ref = channels.parse_channel_link(args.link)
        scope, scope_value = _scope(args)
        if not 0 <= args.max_comments <= 5000:
            raise ValueError('Kommentare pro Video: bitte eine ganze Zahl von 0 bis 5000.')
        quiet = _quiet(args.quiet)
        from .. import repo
        with al._write(cfg.db_path) as connection, repo.atomic(connection) as db:
            mode, account_id, notes = _account(db, ref, args)
            group_id, group_note = _group(db, args.group)
            if group_note:
                notes.append(group_note)
            target = channels.create_channel(db, ref, scope=scope, scope_value=scope_value, comments=args.max_comments,
                                             group_id=group_id, account_mode=mode, account_id=account_id,
                                             pace_profile=PACE[args.pace], interval_min=args.interval, observe=args.watch,
                                             quiet_hours=quiet)
        print(f'Kanal angelegt (Nummer {target["id"]}): {channels.PLATFORM_NAMES[ref.platform]} {ref.display}, '
              f'{_scope_text(target)}, Tempo {chanlogic.PROFILE_LABELS[PACE[args.pace]]}.')
        for note in [*notes, *target.get('notes', [])]:
            print('Hinweis: ' + note)
        print('Der Hintergrunddienst holt zuerst die Videoliste und arbeitet sie dann in Paketen ab.')
        return 0
    target = _find(cfg, args.id)
    if target is None:
        return _missing(args.id)
    if action == 'status':
        return _status(cfg, target)
    name = {'pause': 'pause', 'resume': 'resume', 'retry-failed': 'requeue', 'remove': 'remove'}[action]
    print(chanlogic.action(cfg.db_path, args.id, name))
    return 0
