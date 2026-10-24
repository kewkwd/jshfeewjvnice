"""CLI `sync push|pull|test`: Abgleich mit der Cloud-Datenbank (Supabase/PostgREST)."""
NAME = 'sync'
HELP = 'Mit der Cloud-Datenbank abgleichen: push (senden), pull (holen), test (prüfen)'

_LABELS = {'videos': 'Videos', 'comments': 'Kommentare', 'video_snapshots': 'Verlaufspunkte',
           'groups': 'Gruppen', 'group_videos': 'Gruppenzuordnungen', 'comment_sentiment': 'Stimmungs-Einschätzungen (KI)',
           'group_people': 'Personen in Gruppen'}


def add_arguments(parser):
    parser.add_argument('action', choices=['push', 'pull', 'test'])
    parser.add_argument('--full', action='store_true', help='Alles senden/holen, nicht nur Neues')
    parser.add_argument('--db', help='Pfad der Datenbank (Standard: wie im Programm)')


def run(args, cfg):
    from .. import sync
    if args.action == 'test':
        result = sync.test()
        print(('OK: ' if result['ok'] else 'Fehler: ') + result['message'])
        for table, state in result['tables'].items():
            print(f'  {_LABELS[table]}: {state}')
        return 0 if result['ok'] else 1
    func = sync.push if args.action == 'push' else sync.pull
    result = func(db_path=args.db or cfg.db_path, full=args.full)
    verb = 'gesendet' if args.action == 'push' else 'geholt/zusammengeführt'
    for table, n in result['counts'].items():
        print(f'  {_LABELS[table]}: {n} {verb}')
    if result.get('warning'):   # z. B. Tabellen fehlen in der Cloud; zwei Leerzeichen, damit das Fenster die Zeile übernimmt
        print('  Hinweis: ' + result['warning'])
    if result['error']:
        print('Fehler: ' + result['error'] + (' (Teilweise erledigt.)' if result['partial'] else ''))
        return 1
    print(f'Fertig: {result["total"]} Zeilen {verb}.')
    return 0
