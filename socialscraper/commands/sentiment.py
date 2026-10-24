"""CLI `sentiment status|estimate|run`: Stimmung der gespeicherten Kommentare als KI-Einschätzung (Claude, Anthropic API)."""
NAME = 'sentiment'
HELP = 'Stimmung der Kommentare bestimmen (KI-Einschätzung): status, estimate (Kostenschätzung), run (starten)'


def add_arguments(parser):
    parser.add_argument('action', choices=['status', 'estimate', 'run'])
    parser.add_argument('--video', help='Nur dieses Video (Schlüssel wie tiktok:7301234567)')
    parser.add_argument('--group', type=int, help='Nur die Videos dieser Gruppe (Nummer)')
    parser.add_argument('--max', type=int, dest='max_comments', help='Höchstens so viele Kommentare in diesem Lauf')
    parser.add_argument('--yes', action='store_true', help='Ohne Rückfrage starten (sonst zeigt run nur die Schätzung)')
    parser.add_argument('--db', help='Pfad der Datenbank (Standard: wie im Programm)')


def _scope(args):
    scope = {}
    if args.video:
        scope['videos'] = [args.video]
    if args.group is not None:
        scope['group'] = args.group
    return scope


def run(args, cfg):
    import pathlib
    import sqlite3

    from .. import aiconfig, guicore, sentiment, sentiment_repo as srepo
    from ..store import Store

    root = guicore.ROOT
    ai = aiconfig.load(root)
    db_path = pathlib.Path(args.db or cfg.db_path)
    scope = _scope(args)
    if args.action == 'status':
        if not db_path.is_file():
            print('Es sind noch keine Daten gespeichert.')
            return 0
        with Store(db_path) as store:
            dist = srepo.distribution(store.db, scope)
            for platform in [r[0] for r in store.db.execute('SELECT DISTINCT platform FROM comments ORDER BY platform')]:
                d = srepo.distribution(store.db, {**scope, 'platform': platform})
                print(f'  {platform}: {d["analyzed"]} von {d["total_comments"]} Kommentaren eingestuft, offen: {d["open"]}')
        print('KI-Einschätzung (Schätzung, nicht gemessen):')
        print(f'  positiv: {dist["positive"]}, neutral: {dist["neutral"]}, negativ: {dist["negative"]}')
        print(f'  eingestuft: {dist["analyzed"]} von {dist["total_comments"]} Kommentaren, offen: {dist["open"]}')
        print('  API-Schlüssel: ' + ('vorhanden' if aiconfig.has_key(ai) else 'fehlt (Einstellungen → KI / Stimmung)'))
        return 0
    try:
        est = sentiment.estimate(db_path, scope, ai, max_comments=args.max_comments)
    except sqlite3.Error:
        print('Fehler: Die Datenbank lässt sich gerade nicht lesen. Bitte gleich noch einmal versuchen.')
        return 1
    print(f'Modell: {est["model"]}')
    for line in sentiment.estimate_lines(est):
        print('  ' + line)
    if args.action == 'estimate':
        return 0
    if not est['to_send']:
        return 0
    if not args.yes:
        print('Es wurde nichts gesendet. Mit --yes startest du die Bestimmung. Dabei werden Kommentartexte an die Anthropic-API geschickt.')
        return 0

    def progress(done, total, info):
        print(f'  {done} von {total} Kommentaren …', flush=True)

    result = sentiment.run(db_path, scope, cfg=ai, max_comments=args.max_comments, progress=progress)
    print(('Fertig: ' if result['status'] == 'ok' else 'Hinweis: ' if result['status'] in ('partial', 'cancelled') else 'Fehler: ')
          + result['message'])
    return 0 if result['status'] in ('ok', 'partial', 'cancelled') else 1
