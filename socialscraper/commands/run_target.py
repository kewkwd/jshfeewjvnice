"""Befehl `run-target ID`: ein Ziel der Automatik sofort ausführen."""
import logging
import os
import pathlib

NAME = 'run-target'
HELP = 'ein Ziel der Automatik sofort ausführen (Nummer aus der Zielliste)'


def add_arguments(p):
    p.add_argument('id', type=int)
    p.add_argument('--root', default=None, help='Projektordner (Standard: aktueller Ordner)')


def _title(cfg, target_id):
    """Lesbarer Name des Ziels („Hashtag #lofi (YouTube)“); ohne Treffer „Ziel 5“."""
    try:
        from .. import autologic, repo
        from ..store import Store
        with Store(cfg.db_path) as store:
            target = repo.get_target(store.db, target_id)
        return autologic.target_title(target) if target else f'Ziel {target_id}'
    except Exception:   # der Name ist nur Schmuck – nie den Abruf verhindern
        return f'Ziel {target_id}'


def run(args, cfg):
    from .. import daemon, runlock, scheduler
    root = pathlib.Path(args.root or os.getcwd()).resolve()
    if args.root:
        os.chdir(root)
    title = _title(cfg, args.id)
    cancel_file = os.environ.get('SS_CANCEL_FILE')   # legt das Fenster an, wenn du „Abbrechen" klickst (guicore.Job)
    cancel = (lambda: pathlib.Path(cancel_file).exists()) if cancel_file else None
    # Dieselbe Sperrdatei wie Dienst und Befehle: am Ort der Datenbank (runlock.root_for_db). Busy -> Exit-Code 4 (cli.main)
    with runlock.acquire(runlock.root_for_db(cfg.db_path), 'cli'):
        result = scheduler.run_target(root, args.id, 'manual', cancel=cancel)
    daemon._push(root)
    names = {'ok': 'fertig', 'partial': 'teilweise fertig', 'failed': 'fehlgeschlagen', 'cancelled': 'abgebrochen'}
    seen, comments = result['videos_seen'], result['comments_new']
    print(f"{title}: {names.get(result['status'], result['status'])} - {seen} {'Video' if seen == 1 else 'Videos'}, "
          f"{result['videos_new']} neu, {comments} {'neuer Kommentar' if comments == 1 else 'neue Kommentare'}")
    if result.get('accounts_used'):
        print('Benutzte Konten: ' + ', '.join(result['accounts_used']))
    if result.get('waiting'):   # Kanal: Ruhezeit, Pause oder Tagesgrenze – der Lauf ist entfallen
        print('Kein Abruf: ' + result['waiting'] + '.')
    if result['error']:
        print(result['error'])
    return {'ok': 0, 'partial': 1, 'failed': 1, 'cancelled': 130}.get(result['status'], 1)
