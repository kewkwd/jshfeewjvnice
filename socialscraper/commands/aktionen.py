import json, logging
from .. import aktionen, repo
from ..store import Store
log = logging.getLogger(__name__)
NAME, HELP = 'aktionen', 'Kommentare liken, melden oder kommentieren'

def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    neu = was.add_parser('neu', help='Aktionen einreihen')
    neu.add_argument('art', choices=list(aktionen.ARTEN))
    neu.add_argument('--plattform', required=True, choices=list(repo.ACCOUNT_PLATFORMS))
    neu.add_argument('--ziel', required=True, metavar='plattform:comment_id', help='mehrere mit Komma')
    neu.add_argument('--video', metavar='plattform:video_id')
    neu.add_argument('--eltern', metavar='comment_id')
    neu.add_argument('--text'); neu.add_argument('--grund'); neu.add_argument('--sterne', type=int)
    neu.add_argument('--konto', type=int, metavar='ID')
    neu.add_argument('--anzahl', type=int, default=1, help='so viele distinkte Konten einreihen')
    neu.add_argument('--welle', action='store_true', help='Zwei-Wellen-Kurve setzen')
    was.add_parser('liste', help='offene Aktionen zeigen')
    start = was.add_parser('start', help='Aktionen abarbeiten')
    start.add_argument('--anzahl', type=int, default=50)
    start.add_argument('--art', choices=list(aktionen.ARTEN))
    start.add_argument('--plattform', choices=list(repo.ACCOUNT_PLATFORMS))
    start.add_argument('--probe', action='store_true')
    was.add_parser('zaehler', help='Zahlen je Zustand')

def run(args, cfg):
    with Store(cfg.db_path) as store:
        db = store.db
        if args.was == 'neu':
            ziele = [z.strip() for z in args.ziel.split(',') if z.strip()]
            neu = 0
            for ziel in ziele:
                voll = ziel if ':' in ziel else f'{args.plattform}:{ziel}'
                if args.anzahl > 1 and args.konto is None:
                    neu += aktionen.enqueue_fanout(db, args.plattform, args.art, voll, args.anzahl,
                                                   video_uid=args.video, eltern_id=args.eltern,
                                                   text=args.text, grund=args.grund, sterne=args.sterne,
                                                   welle=args.welle)
                else:
                    if aktionen.enqueue(db, args.plattform, args.art, voll, account_id=args.konto,
                                        video_uid=args.video, eltern_id=args.eltern, text=args.text,
                                        grund=args.grund, sterne=args.sterne):
                        neu += 1
                    if args.welle:
                        aktionen.plane_welle(db, voll)
            print(f'{neu} Aktion(en) eingereiht (von {len(ziele)}).')
            return 0
        if args.was == 'liste':
            for a in aktionen.pending(db, limit=200):
                print(f"[{a['id']:>5}] {a['platform']:<10} {a['art']:<10} {a['ziel_uid']}")
            return 0
        if args.was == 'zaehler':
            print(json.dumps(aktionen.counts(db), ensure_ascii=False))
            return 0
    ergebnis = aktionen.run(cfg.db_path, limit=args.anzahl, art=args.art, platform=args.plattform,
                            dry=args.probe, cfg=cfg)
    print('fertig: ' + json.dumps(ergebnis, ensure_ascii=False))
    if ergebnis.get('abgebrochen'):
        return 130
    if ergebnis.get('gruende') and not ergebnis.get('erledigt'):
        return 3
    return 0 if ergebnis['fehler'] == 0 else 1