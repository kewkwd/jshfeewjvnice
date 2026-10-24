import json, logging
from .. import warmup
log = logging.getLogger(__name__)
NAME, HELP = 'aufwaermen', 'Konten aufwärmen (Art frei wählbar)'

def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    setzen = was.add_parser('setzen', help='Aufwärm-Art eines Kontos festlegen')
    setzen.add_argument('--konto', type=int, required=True)
    setzen.add_argument('--modus', choices=list(warmup.MODI), default='normal')
    setzen.add_argument('--umfang', choices=list(warmup.UMFANG), default='liken')
    setzen.add_argument('--tage', type=int)
    was.add_parser('zustand', help='Aufwärm-Zustand aller Konten')
    start = was.add_parser('start', help='fällige Sitzungen ausführen')
    start.add_argument('--konto', type=int); start.add_argument('--probe', action='store_true')
    app = was.add_parser('app-phase', help='App-Phase (Emulator) setzen')
    app.add_argument('--konto', type=int, required=True); app.add_argument('--tage', type=int, default=None)

def run(args, cfg):
    if args.was == 'setzen':
        print(json.dumps(warmup.setzen(cfg.db_path, args.konto, modus=args.modus, umfang=args.umfang,
                                       tage=args.tage), ensure_ascii=False))
        return 0
    if args.was == 'app-phase':
        print(json.dumps(warmup.setzen(cfg.db_path, args.konto,
                                       phase='web' if args.tage == 0 else 'app', app_tage=args.tage),
                         ensure_ascii=False))
        return 0
    if args.was == 'zustand':
        for z in warmup.zustand(cfg.db_path):
            print(f"[{z['id']:>4}] {z['platform']:<10} {z['label']:<20} {z['warmup_modus']}/{z['warmup_umfang']} "
                  f"Phase {z.get('phase') or 'web'} Wechsel {(z.get('app_bis') or '')[:10]} "
                  f"({z.get('warmup_sitzungen') or 0} Sitzungen)")
        return 0
    bericht = warmup.run(cfg.db_path, konto_id=args.konto, cfg=cfg, trocken=args.probe, melden=print)
    print('fertig: ' + json.dumps(bericht, ensure_ascii=False))
    return 0 if bericht['fehler'] == 0 else 1
